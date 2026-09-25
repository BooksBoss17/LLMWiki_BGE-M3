from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = ROOT / "learn" / "exercise-solution-curation" / "scripts" / "manage_curation_campaign.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_png(path: Path, *, width: int, height: int, marker: int) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    pixel = bytes((marker % 256, 20, 30, 255))
    rows = b"".join(b"\x00" + pixel * width for _ in range(height))
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def run(
    kb: Path,
    *args: object,
    expected: int = 0,
    env: dict[str, str] | None = None,
) -> dict:
    process_env = os.environ.copy()
    process_env.update(env or {})
    process = subprocess.run(
        [sys.executable, str(CAMPAIGN), "--kb-root", str(kb), *(str(value) for value in args)],
        cwd=str(ROOT.parent),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
        env=process_env,
    )
    if process.returncode != expected:
        raise AssertionError(
            f"unexpected return code {process.returncode} != {expected}\n"
            f"stdout={process.stdout}\nstderr={process.stderr}"
        )
    return json.loads(process.stdout)


def read_task_card(claim: dict) -> dict:
    return json.loads(Path(claim["task_card"]).read_text(encoding="utf-8"))


def write_report(
    kb: Path,
    question_id: str,
    target_path: str,
    *,
    mode: str = "apply",
    rolled_back: bool = False,
    current_hash: str | None = None,
    include_validations: bool = True,
) -> Path:
    target = kb / target_path
    current_hash = current_hash or digest(target)
    report_dir = kb / "skills" / "_ops" / "runtime" / "reports" / "role_d_curation" / f"{question_id}_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "ok": True,
        "mode": mode,
        "schema_version": 2,
        "model_profile": "strong",
        "question_id": question_id,
        "target_path": target_path,
        "expected_sha256": digest(target),
        "new_sha256": current_hash,
        "applied": mode == "apply",
        "rolled_back": rolled_back,
        "validations": [],
    }
    if include_validations:
        report["validations"] = [
            {"command": ["python", "validate_exercise_rag_compat.py"], "returncode": 0, "ok": True},
            {"command": ["python", "audit_exercise_images.py"], "returncode": 0, "ok": True},
        ]
    path = report_dir / "curation_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


class CurationCampaignTests(unittest.TestCase):
    def make_fixture(self, kb: Path) -> tuple[Path, list[dict]]:
        source = kb / "source-library" / "source.docx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"synthetic source")
        items: list[dict] = []
        for number in range(1, 10):
            question_id = f"MC{number:07d}"
            target_path = f"raw/exercises/demo/{question_id}.md"
            target = kb / target_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# {question_id}\n\n## 题目\n\n题目{number}\n", encoding="utf-8")
            items.append({
                "question_id": question_id,
                "status": "待证据阻断",
                "reasons": ["synthetic_issue"],
                "target_path": target_path,
                "current_sha256": digest(target),
                "source_title": "合成原件",
                "source_path": "../source-library/source.docx" if number < 9 else "",
                "source_question_no": str(number),
                "report_references": [],
                "apply_evidence": None,
            })
        fixed_report = write_report(kb, "MC0000001", items[0]["target_path"])
        items[0]["status"] = "已修复"
        items[0]["apply_evidence"] = {
            "report": fixed_report.relative_to(kb).as_posix(),
            "new_sha256": digest(kb / items[0]["target_path"]),
            "validation_count": 2,
        }
        ledger = kb / "ledger.json"
        ledger.write_text(json.dumps({"count": len(items), "items": items}, ensure_ascii=False), encoding="utf-8")
        manifest_dir = kb / "skills" / "_ops" / "runtime" / "reports" / "role_d_curation" / "source_groups" / "synthetic"
        manifest_dir.mkdir(parents=True)
        manifest = {
            "source_path": "source-library/source.docx",
            "question_count": 8,
            "mapped_target_count": 8,
            "questions": [
                {"question_id": item["question_id"], "source_question_no": item["source_question_no"], "payload": "bounded"}
                for item in items[:8]
            ],
        }
        (manifest_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return ledger, items

    def test_init_groups_deterministically_and_claim_resumes_same_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, _ = self.make_fixture(kb)
            initialized = run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic", "--batch-size", "5",
                "--teacher-authorized", "--json",
            )
            self.assertEqual(initialized["ledger_count"], 9)
            self.assertEqual(initialized["applied"], 1)
            self.assertEqual(initialized["remaining"], 8)
            self.assertEqual(initialized["curation_batches"], 2)
            self.assertEqual(initialized["source_resolution_batches"], 1)
            self.assertEqual(initialized["source_manifest_count"], 1)

            first = run(kb, "claim", "--campaign", "synthetic", "--json")
            second = run(kb, "claim", "--campaign", "synthetic", "--json")
            first_card = read_task_card(first)
            second_card = read_task_card(second)
            self.assertEqual(first["batch_id"], second["batch_id"])
            self.assertFalse(first["resumed"])
            self.assertTrue(second["resumed"])
            self.assertEqual(len(first_card["items"]), 5)
            self.assertTrue(first_card["stop_after_batch"])
            self.assertTrue(first_card["source"]["exists"])
            self.assertNotIn("items", first)
            self.assertLess(len(json.dumps(first, ensure_ascii=False).encode("utf-8")), 16 * 1024)
            self.assertLess(Path(first["task_card"]).stat().st_size, 64 * 1024)
            batch_manifest = json.loads(Path(first_card["source"]["batch_manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(batch_manifest["question_count"], 5)
            self.assertEqual(len(batch_manifest["questions"]), 5)
            self.assertTrue(all("relationships" not in question for question in batch_manifest["questions"]))
            self.assertEqual(first_card["work_unit_kind"], second_card["work_unit_kind"])

    def test_completed_runner_must_start_a_new_task_before_next_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "1", "--teacher-authorized", "--json",
            )
            runner_a = {"CODEX_THREAD_ID": "thread-a"}
            first = run(kb, "claim", "--campaign", "synthetic", "--json", env=runner_a)
            question_id = read_task_card(first)["items"][0]["question_id"]
            target_path = next(value["target_path"] for value in items if value["question_id"] == question_id)
            report = write_report(kb, question_id, target_path)
            run(
                kb,
                "record", "--campaign", "synthetic", "--question", question_id,
                "--status", "applied", "--evidence", report, "--json",
                env=runner_a,
            )

            blocked = run(kb, "claim", "--campaign", "synthetic", "--json", env=runner_a)
            self.assertTrue(blocked["new_task_required"])
            self.assertNotIn("batch_id", blocked)

            next_task = run(
                kb,
                "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-b"},
            )
            self.assertEqual(next_task["batch_id"], "batch-0002")

    def test_prepare_evidence_deduplicates_and_splits_full_resolution_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            question_id = items[1]["question_id"]
            manifest_path = (
                kb / "skills/_ops/runtime/reports/role_d_curation/source_groups/synthetic/manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            relationships = []
            original_hashes: dict[str, str] = {}
            for index in range(17):
                image = kb / f"source-images/image-{index:02d}.png"
                write_png(image, width=index + 2, height=3, marker=index)
                original_hashes[str(image)] = digest(image)
                relationships.append({
                    "relationship_id": f"rId{index + 1}",
                    "target": f"media/image-{index:02d}.png",
                    "extracted_path": image.relative_to(kb).as_posix(),
                    "sha256": digest(image),
                    "bytes": image.stat().st_size,
                })
            relationships.append(dict(relationships[0], relationship_id="rId-duplicate"))
            question["relationships"] = relationships
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "1", "--teacher-authorized", "--json",
            )
            prepared = run(
                kb,
                "prepare-evidence", "--campaign", "synthetic",
                "--question", question_id, "--json",
                env={"CODEX_THREAD_ID": "thread-evidence"},
            )

            self.assertEqual(prepared["unique_image_count"], 17)
            self.assertEqual(prepared["segments_total"], 2)
            self.assertEqual(prepared["segment_image_counts"], [16, 1])
            evidence_root = Path(prepared["ledger"]).parent
            segment_paths = sorted((evidence_root / "segments").glob("segment-*.json"))
            self.assertEqual(len(segment_paths), 2)
            self.assertTrue(all(len(json.loads(path.read_text(encoding="utf-8"))["assets"]) <= 16 for path in segment_paths))
            self.assertEqual(
                original_hashes,
                {path: digest(Path(path)) for path in original_hashes},
            )
            budget = run(kb, "budget-status", "--campaign", "synthetic", "--json")
            self.assertEqual(len(budget["batches"]), 1)
            batch_budget = next(value for value in budget["batches"] if value["batch_id"] == prepared["batch_id"])
            self.assertEqual(batch_budget["segments_total"], 2)
            self.assertLessEqual(batch_budget["max_estimated_context_ratio"], 0.60)
            self.assertFalse(batch_budget["over_budget"])

    def test_claim_and_record_evidence_expose_one_segment_per_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            question_id = items[1]["question_id"]
            manifest_path = (
                kb / "skills/_ops/runtime/reports/role_d_curation/source_groups/synthetic/manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            image = kb / "source-images/evidence.png"
            write_png(image, width=32, height=24, marker=7)
            question["relationships"] = [{
                "relationship_id": "rId1",
                "target": "media/evidence.png",
                "extracted_path": image.relative_to(kb).as_posix(),
                "sha256": digest(image),
                "bytes": image.stat().st_size,
            }]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "1", "--teacher-authorized", "--json",
            )
            run(
                kb,
                "prepare-evidence", "--campaign", "synthetic", "--question", question_id,
                "--json", env={"CODEX_THREAD_ID": "thread-a"},
            )

            claimed = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertEqual(claimed["work_unit_kind"], "evidence_segment")
            card = json.loads(Path(claimed["task_card"]).read_text(encoding="utf-8"))
            self.assertEqual(card["unique_image_count"], 1)
            self.assertLessEqual(card["unique_image_count"], 16)
            segment = json.loads(Path(card["segment_manifest"]).read_text(encoding="utf-8"))
            evidence = kb / "segment-evidence.json"
            evidence.write_text(json.dumps({
                "schema_version": 2,
                "question_id": question_id,
                "segment_id": segment["segment_id"],
                "segment_manifest_sha256": digest(Path(card["segment_manifest"])),
                "assets": [
                    {"source_sha256": asset["source_sha256"], "status": "confirmed"}
                    for asset in segment["assets"]
                ],
            }, ensure_ascii=False), encoding="utf-8")
            recorded = run(
                kb,
                "record-evidence", "--campaign", "synthetic", "--question", question_id,
                "--evidence", evidence, "--json",
                env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertEqual(recorded["evidence_stage"], "complete")
            resumed = run(
                kb,
                "prepare-evidence", "--campaign", "synthetic", "--question", question_id,
                "--json", env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertEqual(resumed["segments_completed"], 1)
            self.assertEqual(resumed["evidence_stage"], "complete")

            blocked = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertTrue(blocked["new_task_required"])
            curation = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-b"},
            )
            self.assertEqual(curation["work_unit_kind"], "curation")
            curation_card = read_task_card(curation)
            self.assertTrue(curation_card["evidence"]["do_not_reopen_all_images"])
            self.assertNotIn("visual_path", json.dumps(curation_card, ensure_ascii=False))

    def test_unresolved_evidence_blocks_curation_and_is_reopened_by_a_new_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            question_id = items[1]["question_id"]
            manifest_path = (
                kb / "skills/_ops/runtime/reports/role_d_curation/source_groups/synthetic/manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            image = kb / "source-images/unresolved.png"
            write_png(image, width=40, height=20, marker=8)
            question["relationships"] = [{
                "relationship_id": "rId1",
                "target": "media/unresolved.png",
                "extracted_path": image.relative_to(kb).as_posix(),
                "sha256": digest(image),
            }]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            run(kb, "init", "--ledger", ledger, "--campaign", "synthetic", "--batch-size", "1", "--json")
            run(kb, "prepare-evidence", "--campaign", "synthetic", "--question", question_id, "--json")
            first = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-unresolved"},
            )
            card = read_task_card(first)
            segment_path = Path(card["segment_manifest"])
            segment = json.loads(segment_path.read_text(encoding="utf-8"))
            evidence = kb / "unresolved-evidence.json"
            evidence.write_text(json.dumps({
                "schema_version": 2,
                "question_id": question_id,
                "segment_id": segment["segment_id"],
                "segment_manifest_sha256": digest(segment_path),
                "assets": [
                    {"source_sha256": asset["source_sha256"], "status": "unresolved"}
                    for asset in segment["assets"]
                ],
            }), encoding="utf-8")
            recorded = run(
                kb, "record-evidence", "--campaign", "synthetic", "--question", question_id,
                "--evidence", evidence, "--json", env={"CODEX_THREAD_ID": "thread-unresolved"},
            )
            self.assertEqual(recorded["evidence_stage"], "blocked")
            self.assertEqual(recorded["segment_status"], "blocked")

            same_runner = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-unresolved"},
            )
            self.assertTrue(same_runner["new_task_required"])
            reopened = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-vlm"},
            )
            self.assertEqual(reopened["work_unit_kind"], "evidence_segment")
            self.assertEqual(read_task_card(reopened)["segment_id"], segment["segment_id"])

    def test_v1_status_is_read_only_and_first_claim_creates_backup_before_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, _ = self.make_fixture(kb)
            initialized = run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "1", "--teacher-authorized", "--json",
            )
            state_path = Path(initialized["state"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["schema_version"] = 1
            for key in (
                "budget_profile", "max_unique_images", "context_budget_ratio",
                "context_window_tokens", "runner_completion_barriers",
            ):
                state.pop(key, None)
            for batch in state["batches"]:
                for key in (
                    "claimed_by_runner", "completed_by_runner", "evidence_stage",
                    "segments_total", "segments_completed", "unique_image_count",
                    "total_pixels", "text_bytes", "active_work_unit",
                ):
                    batch.pop(key, None)
            state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            run(kb, "status", "--campaign", "synthetic", "--json")
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["schema_version"], 1)
            self.assertEqual(list(state_path.parent.glob("campaign.v1.*.json")), [])

            claimed = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "migration-thread"},
            )
            self.assertEqual(claimed["batch_id"], "batch-0001")
            migrated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], 2)
            backups = list(state_path.parent.glob("campaign.v1.*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["schema_version"], 1)

    def test_new_runner_takes_over_incomplete_work_and_old_runner_cannot_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "1", "--teacher-authorized", "--json",
            )
            first = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-old"},
            )
            takeover = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-new"},
            )
            self.assertEqual(first["work_unit_id"], takeover["work_unit_id"])
            self.assertEqual(takeover["claimed_by_runner"], "thread-new")
            card = read_task_card(takeover)
            question_id = card["items"][0]["question_id"]
            target_path = next(value["target_path"] for value in items if value["question_id"] == question_id)
            report = write_report(kb, question_id, target_path)

            rejected = run(
                kb,
                "record", "--campaign", "synthetic", "--question", question_id,
                "--status", "applied", "--evidence", report, "--json",
                expected=2, env={"CODEX_THREAD_ID": "thread-old"},
            )
            self.assertEqual(rejected["type"], "PermissionError")
            self.assertIn("runner_mismatch", rejected["error"])

            accepted = run(
                kb,
                "record", "--campaign", "synthetic", "--question", question_id,
                "--status", "applied", "--evidence", report, "--json",
                env={"CODEX_THREAD_ID": "thread-new"},
            )
            self.assertEqual(accepted["status"], "applied")

    def test_missing_runner_keeps_legacy_compatibility_with_explicit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, _ = self.make_fixture(kb)
            run(kb, "init", "--ledger", ledger, "--campaign", "synthetic", "--json")
            claimed = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": ""},
            )
            self.assertEqual(claimed["work_unit_kind"], "curation")
            self.assertIn("runner_id_unavailable_thread_gate_disabled", claimed["warnings"])

    def test_changed_source_hash_marks_prepared_evidence_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            question_id = items[1]["question_id"]
            manifest_path = (
                kb / "skills/_ops/runtime/reports/role_d_curation/source_groups/synthetic/manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            image = kb / "source-images/changing.png"
            write_png(image, width=8, height=8, marker=1)
            question["relationships"] = [{
                "relationship_id": "rId1",
                "target": "media/changing.png",
                "extracted_path": image.relative_to(kb).as_posix(),
                "sha256": digest(image),
            }]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "1", "--json",
            )
            first = run(
                kb,
                "prepare-evidence", "--campaign", "synthetic", "--question", question_id,
                "--json", env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertFalse(first["stale"])

            write_png(image, width=9, height=8, marker=2)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            question["relationships"][0]["sha256"] = digest(image)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            second = run(
                kb,
                "prepare-evidence", "--campaign", "synthetic", "--question", question_id,
                "--json", env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertTrue(second["stale"])
            self.assertEqual(second["evidence_stage"], "pending")

    def test_new_source_asset_adds_supplemental_segment_without_reopening_completed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            question_id = items[1]["question_id"]
            manifest_path = (
                kb / "skills/_ops/runtime/reports/role_d_curation/source_groups/synthetic/manifest.json"
            )
            first_image = kb / "source-images/first.png"
            second_image = kb / "source-images/supplemental.png"
            write_png(first_image, width=12, height=8, marker=1)
            write_png(second_image, width=14, height=9, marker=2)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            question["relationships"] = [{
                "relationship_id": "rId1",
                "target": "media/first.png",
                "extracted_path": first_image.relative_to(kb).as_posix(),
                "sha256": digest(first_image),
            }]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            run(kb, "init", "--ledger", ledger, "--campaign", "synthetic", "--batch-size", "1", "--json")
            prepared = run(kb, "prepare-evidence", "--campaign", "synthetic", "--question", question_id, "--json")
            claimed = run(
                kb, "claim", "--campaign", "synthetic", "--json",
                env={"CODEX_THREAD_ID": "thread-first"},
            )
            card = read_task_card(claimed)
            first_segment_path = Path(card["segment_manifest"])
            first_segment = json.loads(first_segment_path.read_text(encoding="utf-8"))
            evidence = kb / "first-evidence.json"
            evidence.write_text(json.dumps({
                "schema_version": 2,
                "question_id": question_id,
                "segment_id": first_segment["segment_id"],
                "segment_manifest_sha256": digest(first_segment_path),
                "assets": [
                    {"source_sha256": asset["source_sha256"], "status": "confirmed"}
                    for asset in first_segment["assets"]
                ],
            }), encoding="utf-8")
            run(
                kb, "record-evidence", "--campaign", "synthetic", "--question", question_id,
                "--evidence", evidence, "--json", env={"CODEX_THREAD_ID": "thread-first"},
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            question["relationships"].append({
                "relationship_id": "rId2",
                "target": "media/supplemental.png",
                "extracted_path": second_image.relative_to(kb).as_posix(),
                "sha256": digest(second_image),
            })
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            supplemental = run(
                kb, "prepare-evidence", "--campaign", "synthetic", "--question", question_id, "--json",
                env={"CODEX_THREAD_ID": "thread-first"},
            )
            self.assertFalse(supplemental["stale"])
            self.assertEqual(supplemental["segments_total"], 2)
            self.assertEqual(supplemental["segments_completed"], 1)
            ledger_payload = json.loads(Path(prepared["ledger"]).read_text(encoding="utf-8"))
            self.assertEqual([value["status"] for value in ledger_payload["segments"]], ["completed", "pending"])
            self.assertEqual(ledger_payload["segments"][0]["segment_manifest_sha256"], digest(first_segment_path))
            supplemental_manifest = json.loads(
                Path(ledger_payload["segments"][1]["manifest"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                [asset["source_sha256"] for asset in supplemental_manifest["assets"]],
                [digest(second_image)],
            )

    def test_identical_source_hash_is_reviewed_once_across_questions_in_a_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            first_id, second_id = items[1]["question_id"], items[2]["question_id"]
            image = kb / "source-images/shared.png"
            write_png(image, width=12, height=12, marker=5)
            manifest_path = (
                kb / "skills/_ops/runtime/reports/role_d_curation/source_groups/synthetic/manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for relationship_id, question_id in (("rId1", first_id), ("rId2", second_id)):
                question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
                question["relationships"] = [{
                    "relationship_id": relationship_id,
                    "target": "media/shared.png",
                    "extracted_path": image.relative_to(kb).as_posix(),
                    "sha256": digest(image),
                }]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "2", "--json",
            )
            first = run(kb, "prepare-evidence", "--campaign", "synthetic", "--question", first_id, "--json")
            second = run(kb, "prepare-evidence", "--campaign", "synthetic", "--question", second_id, "--json")
            self.assertEqual(first["unique_image_count"], 1)
            self.assertEqual(second["unique_image_count"], 0)
            self.assertEqual(second["segments_total"], 0)
            budget = run(kb, "budget-status", "--campaign", "synthetic", "--json")
            batch = next(value for value in budget["batches"] if value["batch_id"] == first["batch_id"])
            self.assertEqual(batch["unique_image_count"], 1)

    def test_single_original_over_budget_blocks_without_downscaling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            question_id = items[1]["question_id"]
            image = kb / "source-images/huge.png"
            write_png(image, width=1, height=1, marker=9)
            payload = bytearray(image.read_bytes())
            payload[16:24] = struct.pack(">II", 100_000, 100_000)
            payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]) & 0xFFFFFFFF)
            image.write_bytes(payload)
            manifest_path = (
                kb / "skills/_ops/runtime/reports/role_d_curation/source_groups/synthetic/manifest.json"
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            question = next(value for value in manifest["questions"] if value["question_id"] == question_id)
            question["relationships"] = [{
                "relationship_id": "rId1",
                "target": "media/huge.png",
                "extracted_path": image.relative_to(kb).as_posix(),
                "sha256": digest(image),
            }]
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            run(
                kb,
                "init", "--ledger", ledger, "--campaign", "synthetic",
                "--batch-size", "1", "--json",
            )
            prepared = run(
                kb,
                "prepare-evidence", "--campaign", "synthetic", "--question", question_id,
                "--json", env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertEqual(prepared["evidence_stage"], "blocked")
            failure = run(
                kb, "claim", "--campaign", "synthetic", "--json", expected=2,
                env={"CODEX_THREAD_ID": "thread-a"},
            )
            self.assertIn("evidence_budget_blocked", failure["error"])
            self.assertIn("never crop or downscale", failure["error"])

    def test_record_is_evidence_backed_idempotent_and_reconcile_recovers_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            run(kb, "init", "--ledger", ledger, "--campaign", "synthetic", "--teacher-authorized", "--json")
            claimed = run(kb, "claim", "--campaign", "synthetic", "--json")
            claimed_card = read_task_card(claimed)
            question_id = claimed_card["items"][0]["question_id"]
            target_path = next(value["target_path"] for value in items if value["question_id"] == question_id)
            report = write_report(kb, question_id, target_path)
            recorded = run(
                kb, "record", "--campaign", "synthetic", "--question", question_id,
                "--status", "applied", "--evidence", report, "--json",
            )
            repeated = run(
                kb, "record", "--campaign", "synthetic", "--question", question_id,
                "--status", "applied", "--evidence", report, "--json",
            )
            self.assertEqual(recorded["status"], "applied")
            self.assertEqual(repeated["counts"]["applied"], 2)

            dry_id = claimed_card["items"][1]["question_id"]
            dry_target = next(value["target_path"] for value in items if value["question_id"] == dry_id)
            write_report(kb, dry_id, dry_target, mode="dry-run")
            reconciled = run(kb, "reconcile", "--campaign", "synthetic", "--json")
            self.assertGreaterEqual(reconciled["counts"]["dry_run_passed"], 1)
            state = json.loads((kb / "skills/_ops/runtime/state/role_d_curation_campaigns/synthetic/campaign.json").read_text(encoding="utf-8"))
            self.assertEqual(state["items"][dry_id]["status"], "dry_run_passed")

    def test_rollback_stale_hash_and_missing_validations_never_complete_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            run(kb, "init", "--ledger", ledger, "--campaign", "synthetic", "--teacher-authorized", "--json")
            claimed = run(kb, "claim", "--campaign", "synthetic", "--json")
            rolled_id, stale_id, invalid_id = [
                value["question_id"] for value in read_task_card(claimed)["items"][:3]
            ]
            lookup = {value["question_id"]: value["target_path"] for value in items}
            write_report(kb, rolled_id, lookup[rolled_id], rolled_back=True)
            write_report(kb, stale_id, lookup[stale_id], current_hash="0" * 64)
            invalid = write_report(kb, invalid_id, lookup[invalid_id], include_validations=False)
            run(kb, "reconcile", "--campaign", "synthetic", "--json")
            state_path = kb / "skills/_ops/runtime/state/role_d_curation_campaigns/synthetic/campaign.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotEqual(state["items"][rolled_id]["status"], "applied")
            self.assertNotEqual(state["items"][stale_id]["status"], "applied")
            error = run(
                kb, "record", "--campaign", "synthetic", "--question", invalid_id,
                "--status", "applied", "--evidence", invalid, "--json", expected=2,
            )
            self.assertFalse(error["ok"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotEqual(state["items"][invalid_id]["status"], "applied")

    def test_source_mapping_moves_unknown_item_into_a_curation_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledger, items = self.make_fixture(kb)
            run(kb, "init", "--ledger", ledger, "--campaign", "synthetic", "--teacher-authorized", "--json")
            unknown = items[-1]["question_id"]
            source = kb / "source-library" / "source.docx"
            mapped = run(
                kb, "record", "--campaign", "synthetic", "--question", unknown,
                "--status", "source-mapped", "--source-path", "source-library/source.docx",
                "--evidence", source, "--json",
            )
            self.assertIsNotNone(mapped["mapped_batch_id"])
            state_path = kb / "skills/_ops/runtime/state/role_d_curation_campaigns/synthetic/campaign.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["items"][unknown]["source_key"], "source-library/source.docx")
            containing = [batch for batch in state["batches"] if unknown in batch["question_ids"]]
            self.assertEqual(len(containing), 1)
            self.assertEqual(containing[0]["kind"], "curation")


if __name__ == "__main__":
    unittest.main()
