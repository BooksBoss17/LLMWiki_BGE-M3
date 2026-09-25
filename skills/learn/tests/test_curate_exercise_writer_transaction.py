from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "learn" / "exercise-solution-curation" / "scripts" / "curate_exercise.py"
SPEC = importlib.util.spec_from_file_location("curate_exercise_writer_transaction", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
curate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(curate)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SimulatedCrash(BaseException):
    pass


class CurateExerciseWriterTransactionTests(unittest.TestCase):
    question_id = "SA0000200"

    def test_atomic_json_write_retries_transient_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original_replace = curate.os.replace
            replace_attempts = 0

            def transient_permission_error(source, destination):
                nonlocal replace_attempts
                replace_attempts += 1
                if replace_attempts < 3:
                    raise PermissionError(13, "injected Windows sharing violation")
                return original_replace(source, destination)

            with (
                patch.object(curate.os, "replace", side_effect=transient_permission_error),
                patch.object(curate.time, "sleep", return_value=None),
            ):
                curate.write_atomic_json(path, {"status": "committed"})

            self.assertEqual(replace_attempts, 3)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"status": "committed"},
            )

    def test_atomic_json_write_fails_closed_after_retry_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            replace_attempts = 0

            def permanent_permission_error(_source, _destination):
                nonlocal replace_attempts
                replace_attempts += 1
                raise PermissionError(13, "injected permanent access denial")

            with (
                patch.object(curate.os, "replace", side_effect=permanent_permission_error),
                patch.object(curate.time, "sleep", return_value=None),
                self.assertRaises(PermissionError),
            ):
                curate.write_atomic_json(path, {"status": "committed"})

            self.assertEqual(
                replace_attempts,
                len(curate.ATOMIC_REPLACE_RETRY_DELAYS_SECONDS) + 1,
            )
            self.assertFalse(path.exists())

    def make_case(self, root: Path, media_modes: tuple[str, ...]) -> dict[str, object]:
        target = root / f"raw/exercises/demo/{self.question_id}.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            "---\n"
            f"id: {self.question_id}\n"
            "knowledge_points:\n"
            "- 力学/牛顿运动定律\n"
            "---\n\n"
            "## 题目\n\n请根据已知条件完成推导。\n\n"
            "## 答案\n\n原答案\n\n"
            "## 详解\n\n原有解析内容完整且可复核。\n",
            encoding="utf-8",
        )
        original_target = target.read_bytes()
        evidence = root / "tmp/tasks/writer-transaction/evidence.txt"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("stable evidence", encoding="utf-8")

        media_files: list[dict[str, str]] = []
        assets: list[str] = []
        original_media: dict[Path, bytes | None] = {}
        for index, mode in enumerate(media_modes, start=1):
            source = evidence.parent / f"source-{index}.png"
            source.write_bytes(f"new-media-{index}".encode())
            target_ref = f"media/{self.question_id}_{index}.png"
            destination = target.parent / target_ref
            original_media[destination] = None
            entry = {
                "source_path": source.relative_to(root).as_posix(),
                "source_sha256": digest(source),
                "target": target_ref,
            }
            if mode == "replace":
                destination.parent.mkdir(parents=True, exist_ok=True)
                old_bytes = f"old-media-{index}".encode()
                destination.write_bytes(old_bytes)
                original_media[destination] = old_bytes
                entry["expected_target_sha256"] = digest(destination)
            elif mode == "reuse":
                destination.parent.mkdir(parents=True, exist_ok=True)
                reused_bytes = source.read_bytes()
                destination.write_bytes(reused_bytes)
                original_media[destination] = reused_bytes
            elif mode != "create":
                raise AssertionError(f"unsupported test mode: {mode}")
            media_files.append(entry)
            assets.append(target_ref)

        image_lines = "\n\n".join(
            f"![事务媒体 {index}]({asset})" for index, asset in enumerate(assets, start=1)
        )
        proposal = {
            "schema_version": 2,
            "model_profile": "strong",
            "question_id": self.question_id,
            "target_path": target.relative_to(root).as_posix(),
            "expected_sha256": digest(target),
            "updates": {
                "answer": "新答案",
                "solution": f"根据题意逐步分析并核验全部条件，得到可复核的新结论。\n\n{image_lines}",
                "assets": assets,
            },
            "media_files": media_files,
            "verification": {
                "source_evidence": [
                    {"path": evidence.relative_to(root).as_posix(), "sha256": digest(evidence)}
                ],
                "stem_complete": True,
                "figure_dependencies_resolved": True,
                "review_passes": 2,
                "non_choice_check": {
                    "status": "confirmed",
                    "summary": "已逐项复核题干条件、推导过程、媒体引用和最终结论，内容保持一致。",
                },
            },
        }
        proposal_path = evidence.parent / "proposal.json"
        proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
        return {
            "target": target,
            "original_target": original_target,
            "original_media": original_media,
            "proposal": proposal_path,
        }

    def invoke(self, root: Path, proposal: Path, *extra_patches: object, dry_run: bool = False):
        captured: list[dict[str, object]] = []
        arguments = [
            "--proposal",
            str(proposal),
            "--dry-run" if dry_run else "--apply",
            "--test-mode",
            "--kb-root",
            str(root),
            "--json",
        ]
        if not dry_run:
            arguments.append("--teacher-authorized")
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    curate,
                    "emit_json",
                    side_effect=lambda payload, error=False: captured.append(payload),
                )
            )
            for active_patch in extra_patches:
                stack.enter_context(active_patch)
            return_code = curate.main(arguments)
        self.assertTrue(captured)
        return return_code, captured[-1]

    def latest_journal(self, root: Path) -> tuple[Path, dict[str, object]]:
        journal_paths = sorted(
            (
                root
                / "skills/_ops/runtime/state/role_d_curation/writer_transactions"
                / self.question_id
            ).glob("*/journal.json")
        )
        self.assertTrue(journal_paths)
        path = journal_paths[-1]
        return path, json.loads(path.read_text(encoding="utf-8"))

    def assert_original_state(self, case: dict[str, object]) -> None:
        target = case["target"]
        assert isinstance(target, Path)
        self.assertEqual(target.read_bytes(), case["original_target"])
        original_media = case["original_media"]
        assert isinstance(original_media, dict)
        for destination, original in original_media.items():
            if original is None:
                self.assertFalse(destination.exists())
            else:
                self.assertEqual(destination.read_bytes(), original)

    def prepare_applied_transaction(
        self,
        root: Path,
        case: dict[str, object],
    ) -> tuple[Path, dict[str, object], Path, dict[str, object]]:
        proposal_path = case["proposal"]
        target = case["target"]
        assert isinstance(proposal_path, Path)
        assert isinstance(target, Path)
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        updated = target.read_text(encoding="utf-8")
        for field, heading in {"answer": "答案", "solution": "详解"}.items():
            if field in proposal["updates"]:
                updated = curate.replace_h2_section(updated, heading, proposal["updates"][field])
        updated = curate.replace_frontmatter_list(updated, "assets", proposal["updates"]["assets"])
        prepared_media: list[dict[str, object]] = []
        for item in proposal["media_files"]:
            source = root / item["source_path"]
            destination = target.parent / item["target"]
            if not destination.exists():
                action = "create"
            elif digest(destination) == item["source_sha256"]:
                action = "reuse"
            else:
                action = "replace"
            prepared_media.append(
                {**item, "source": source, "destination": destination, "action": action}
            )
        journal_path, journal = curate.prepare_writer_transaction(
            kb_root=root,
            question_id=self.question_id,
            transaction_id="fault-boundary-transaction",
            target_rel=target.relative_to(root).as_posix(),
            target=target,
            expected_target_sha256=proposal["expected_sha256"],
            updated=updated,
            prepared_media=prepared_media,
        )
        curate.apply_writer_transaction(kb_root=root, journal_path=journal_path, journal=journal)
        receipt_out = root / "skills/_ops/runtime/receipts/fault-boundary/apply.json"
        receipt_payload = {
            "schema_version": 3,
            "kind": "apply_receipt",
            "status": "applied",
            "question_id": self.question_id,
            "files": curate.transaction_committed_files(journal_path=journal_path, journal=journal),
            "validations": {"ok": True, "checks": [{"ok": True, "status": "test"}]},
            "created_at": curate.utc_now(),
        }
        return journal_path, journal, receipt_out, receipt_payload

    def assert_committed_state(
        self,
        root: Path,
        case: dict[str, object],
        journal_path: Path,
        receipt_out: Path,
    ) -> None:
        target = case["target"]
        proposal_path = case["proposal"]
        assert isinstance(target, Path)
        assert isinstance(proposal_path, Path)
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        self.assertIn("## 答案\n\n新答案", target.read_text(encoding="utf-8"))
        for item in proposal["media_files"]:
            self.assertEqual((target.parent / item["target"]).read_bytes(), (root / item["source_path"]).read_bytes())
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["status"], "committed")
        decision_path = journal_path.parent / curate.COMMIT_DECISION_NAME
        self.assertTrue(decision_path.is_file())
        self.assertEqual(journal["commit_decision"]["sha256"], digest(decision_path))
        self.assertTrue(receipt_out.is_file())
        receipt = json.loads(receipt_out.read_text(encoding="utf-8"))
        decision_ref = receipt["validations"]["writer_transaction"]
        self.assertEqual(decision_ref["path"], str(decision_path))
        self.assertEqual(decision_ref["sha256"], digest(decision_path))
        self.assertEqual(decision_ref["transaction_id"], journal["transaction_id"])
        self.assertEqual(
            decision_ref["journal"]["sha256_before_decision"],
            json.loads(decision_path.read_text(encoding="utf-8"))["journal"]["sha256_before_decision"],
        )

    def test_successful_apply_commits_full_pre_post_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = self.make_case(root, ("replace", "create"))
            return_code, result = self.invoke(root, case["proposal"])
            self.assertEqual(return_code, 0)
            self.assertTrue(result["applied"])
            journal_path, journal = self.latest_journal(root)
            self.assertEqual(journal["status"], "committed")
            self.assertEqual(
                [event["status"] for event in journal["history"]],
                ["prepared", "applying", "commit_decided", "committed"],
            )
            decision_path = journal_path.parent / curate.COMMIT_DECISION_NAME
            self.assertTrue(decision_path.is_file())
            self.assertEqual(journal["commit_decision"]["sha256"], digest(decision_path))
            for record in journal["files"]:
                self.assertEqual(record["apply_status"], "applied")
                for snapshot_name in ("pre", "post"):
                    snapshot = record[snapshot_name]
                    if snapshot["exists"]:
                        copy_path = journal_path.parent / "copies" / snapshot["copy_path"]
                        self.assertTrue(copy_path.is_file())
                        self.assertEqual(digest(copy_path), snapshot["sha256"])

    def test_create_replace_and_reuse_drift_fail_closed_before_media_write(self) -> None:
        for media_mode in ("create", "replace", "reuse"):
            with self.subTest(media_mode=media_mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = self.make_case(root, (media_mode,))
                original_apply = curate.apply_transaction_file
                injected = False

                def inject_drift(**kwargs):
                    nonlocal injected
                    record = kwargs["record"]
                    if record["kind"] == "media" and not injected:
                        destination = curate.transaction_file_path(root, record)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(b"external-drift")
                        injected = True
                    return original_apply(**kwargs)

                return_code, result = self.invoke(
                    root,
                    case["proposal"],
                    patch.object(curate, "apply_transaction_file", side_effect=inject_drift),
                )
                self.assertEqual(return_code, 2)
                self.assertEqual(result["transaction_journal"]["status"], "recovery_required")
                self.assertEqual(case["target"].read_bytes(), case["original_target"])
                journal_path, journal = self.latest_journal(root)
                statuses = [event["status"] for event in journal["history"]]
                self.assertIn("recovery_required", statuses)
                self.assertNotIn("committed", statuses)
                self.assertFalse(any(record["kind"] == "execution_receipt" for record in journal["files"]))
                self.assertFalse((root / "skills/_ops/runtime/receipts").exists())

                dry_code, dry_result = self.invoke(root, case["proposal"], dry_run=True)
                self.assertEqual(dry_code, 2)
                self.assertIn("unfinished writer transaction", dry_result["message"])
                self.assertEqual(
                    json.loads(journal_path.read_text(encoding="utf-8"))["status"],
                    "recovery_required",
                )

    def test_create_is_atomic_when_external_file_appears_after_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = self.make_case(root, ("create",))
            original_link = curate.os.link
            injected = False

            def race_link(source, destination, *args, **kwargs):
                nonlocal injected
                if not injected and Path(destination).name == f"{self.question_id}_1.png":
                    Path(destination).write_bytes(b"external-winner")
                    injected = True
                return original_link(source, destination, *args, **kwargs)

            return_code, result = self.invoke(
                root,
                case["proposal"],
                patch.object(curate.os, "link", side_effect=race_link),
            )
            self.assertEqual(return_code, 2)
            destination = next(iter(case["original_media"]))
            self.assertEqual(destination.read_bytes(), b"external-winner")
            self.assertEqual(case["target"].read_bytes(), case["original_target"])
            self.assertEqual(result["transaction_journal"]["status"], "recovery_required")
            _, journal = self.latest_journal(root)
            self.assertNotIn("committed", [event["status"] for event in journal["history"]])

    def test_update_and_replace_races_preserve_external_winner_bytes(self) -> None:
        for target_kind in ("main", "media"):
            for event in ("after_precondition_before_hold", "after_hold_before_install"):
                with (
                    self.subTest(target_kind=target_kind, event=event),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    case = self.make_case(root, ("replace",))
                    target = case["target"]
                    assert isinstance(target, Path)
                    media_target = next(iter(case["original_media"]))
                    raced_path = target if target_kind == "main" else media_target
                    winner = f"external-winner-{target_kind}-{event}".encode()
                    injected = False

                    def inject_race(hook_event: str, *, destination: Path, hold_path: Path | None) -> None:
                        nonlocal injected
                        if hook_event == event and destination.resolve() == raced_path.resolve() and not injected:
                            destination.write_bytes(winner)
                            injected = True

                    return_code, result = self.invoke(
                        root,
                        case["proposal"],
                        patch.object(curate, "transaction_fault_hook", side_effect=inject_race),
                    )
                    self.assertEqual(return_code, 2, (target_kind, event, result, injected))
                    self.assertTrue(injected)
                    self.assertEqual(raced_path.read_bytes(), winner)
                    self.assertEqual(result["transaction_journal"]["status"], "recovery_required")
                    journal_path, journal = self.latest_journal(root)
                    raced_record = next(
                        record
                        for record in journal["files"]
                        if curate.transaction_file_path(root, record).resolve() == raced_path.resolve()
                    )
                    pre_copy = curate.transaction_snapshot_path(journal_path.parent, raced_record["pre"])
                    self.assertEqual(digest(pre_copy), raced_record["pre"]["sha256"])
                    hold_path = curate.transaction_hold_path(journal_path.parent, raced_record)
                    if event == "after_hold_before_install":
                        self.assertTrue(hold_path.is_file())
                        self.assertEqual(digest(hold_path), raced_record["pre"]["sha256"])
                    self.assertFalse((journal_path.parent / curate.COMMIT_DECISION_NAME).exists())

    def test_faults_in_main_and_multiple_media_compensation_are_recoverable(self) -> None:
        for failed_restore_step in (1, 2, 3):
            with self.subTest(failed_restore_step=failed_restore_step), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = self.make_case(root, ("replace", "create"))
                original_compensate = curate.compensate_transaction_file
                restore_calls = 0

                def fail_before_commit_decision(**kwargs):
                    raise OSError("injected pre-decision failure")

                def fail_selected_restore(**kwargs):
                    nonlocal restore_calls
                    restore_calls += 1
                    if restore_calls == failed_restore_step:
                        raise OSError(f"injected restore failure {failed_restore_step}")
                    return original_compensate(**kwargs)

                return_code, result = self.invoke(
                    root,
                    case["proposal"],
                    patch.object(curate, "publish_commit_decision", side_effect=fail_before_commit_decision),
                    patch.object(curate, "compensate_transaction_file", side_effect=fail_selected_restore),
                )
                self.assertEqual(return_code, 2)
                self.assertEqual(result["transaction_journal"]["status"], "recovery_required")
                journal_path, journal = self.latest_journal(root)
                failed_records = [
                    record for record in journal["files"]
                    if record["compensation_status"] == "recovery_required"
                ]
                self.assertEqual(len(failed_records), 1)
                failed_record = failed_records[0]
                failed_path = curate.transaction_file_path(root, failed_record)
                self.assertTrue(curate.state_matches(curate.file_state(failed_path), failed_record["post"]))

                recovered = curate.recover_incomplete_writer_transactions(
                    kb_root=root,
                    question_id=self.question_id,
                    allow_recovery=True,
                )
                self.assertEqual(len(recovered), 1)
                self.assert_original_state(case)
                recovered_journal = json.loads(journal_path.read_text(encoding="utf-8"))
                self.assertEqual(recovered_journal["status"], "compensated")
                statuses = [event["status"] for event in recovered_journal["history"]]
                self.assertEqual(
                    statuses,
                    [
                        "prepared",
                        "applying",
                        "compensating",
                        "recovery_required",
                        "compensating",
                        "compensated",
                    ],
                )

    def test_crash_boundaries_before_and_after_commit_decision_recover_in_correct_direction(self) -> None:
        boundaries = (
            "before_decision",
            "decision_published",
            "journal_marked",
            "receipt_recorded",
            "receipt_published",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = self.make_case(root, ("replace", "create", "reuse"))
                journal_path, journal, receipt_out, receipt_payload = self.prepare_applied_transaction(root, case)
                original_publish = curate.publish_commit_decision
                original_attach = curate.attach_commit_decision_to_journal
                original_prepare_receipt = curate.prepare_commit_receipt_record
                original_transition = curate.transition_transaction

                def crash_before_decision(**kwargs):
                    raise SimulatedCrash("before durable decision")

                def crash_after_decision(**kwargs):
                    original_publish(**kwargs)
                    raise SimulatedCrash("after durable decision")

                def crash_after_journal_marker(**kwargs):
                    original_attach(**kwargs)
                    raise SimulatedCrash("after journal marker")

                def crash_after_receipt_record(**kwargs):
                    original_prepare_receipt(**kwargs)
                    raise SimulatedCrash("after receipt record")

                def crash_before_final_transition(journal_path_arg, journal_arg, status, **kwargs):
                    if status == "committed":
                        raise SimulatedCrash("receipt published before journal final transition")
                    return original_transition(journal_path_arg, journal_arg, status, **kwargs)

                boundary_patch = {
                    "before_decision": patch.object(
                        curate, "publish_commit_decision", side_effect=crash_before_decision
                    ),
                    "decision_published": patch.object(
                        curate, "publish_commit_decision", side_effect=crash_after_decision
                    ),
                    "journal_marked": patch.object(
                        curate, "attach_commit_decision_to_journal", side_effect=crash_after_journal_marker
                    ),
                    "receipt_recorded": patch.object(
                        curate, "prepare_commit_receipt_record", side_effect=crash_after_receipt_record
                    ),
                    "receipt_published": patch.object(
                        curate, "transition_transaction", side_effect=crash_before_final_transition
                    ),
                }[boundary]
                with boundary_patch, self.assertRaises(SimulatedCrash):
                    curate.commit_writer_transaction(
                        kb_root=root,
                        journal_path=journal_path,
                        journal=journal,
                        receipt_out=receipt_out,
                        receipt_payload=receipt_payload,
                    )

                crashed_journal = json.loads(journal_path.read_text(encoding="utf-8"))
                decision_path = journal_path.parent / curate.COMMIT_DECISION_NAME
                if boundary == "before_decision":
                    self.assertFalse(decision_path.exists())
                else:
                    self.assertTrue(decision_path.is_file())
                if boundary == "decision_published":
                    self.assertEqual(crashed_journal["status"], "applying")
                if boundary == "journal_marked":
                    self.assertEqual(crashed_journal["status"], "commit_decided")
                    self.assertFalse(any(record["kind"] == "execution_receipt" for record in crashed_journal["files"]))
                if boundary == "receipt_recorded":
                    self.assertEqual(crashed_journal["status"], "commit_decided")
                    self.assertTrue(any(record["kind"] == "execution_receipt" for record in crashed_journal["files"]))
                    self.assertFalse(receipt_out.exists())
                if boundary == "receipt_published":
                    self.assertEqual(crashed_journal["status"], "commit_decided")
                    self.assertTrue(receipt_out.is_file())

                recovered = curate.recover_incomplete_writer_transactions(
                    kb_root=root,
                    question_id=self.question_id,
                    allow_recovery=True,
                )
                self.assertEqual(len(recovered), 1)
                if boundary == "before_decision":
                    self.assert_original_state(case)
                    self.assertFalse(receipt_out.exists())
                    self.assertEqual(
                        json.loads(journal_path.read_text(encoding="utf-8"))["status"],
                        "compensated",
                    )
                else:
                    self.assert_committed_state(root, case, journal_path, receipt_out)


if __name__ == "__main__":
    unittest.main()
