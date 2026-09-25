from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_batch_contract.py"
ADAPTER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_v1_mission_adapter.py"
SPEC = importlib.util.spec_from_file_location("import_batch_contract", SCRIPT)
assert SPEC and SPEC.loader
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def load_adapter():
    spec = importlib.util.spec_from_file_location("import_v1_mission_adapter", ADAPTER_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHA = "a" * 64


def task_card(owner_skill: str = "exercise-bank-import", payload_class: str = "bounded_review") -> dict:
    return {
        "schema_version": 1,
        "task_id": "task-001",
        "batch_id": "batch-001",
        "attempt_id": "attempt-001",
        "attempt_no": 1,
        "worker_instance_id": "worker-001",
        "fork_context": False,
        "queue_ref": {
            "path_id": "workspace.tmp",
            "relative_path": "tasks/task-001/queue.json",
            "sha256": "b" * 64,
            "size_bytes": 100,
        },
        "queue_ordinal": 0,
        "owner_skill": owner_skill,
        "agent_role": "reviewer",
        "payload_class": payload_class,
        "batch_metrics": {"item_count": 1, "page_count": 1, "unresolved_review_blocks": 1},
        "input_refs": [
            {
                "path_id": "workspace.tmp",
                "relative_path": "tasks/task-001/input.json",
                "sha256": SHA,
                "size_bytes": 10,
            }
        ],
        "result_path": {
            "path_id": "workspace.tmp",
            "relative_path": "tasks/task-001/results/attempt-001.json",
        },
    }


def compact_result(card: dict) -> dict:
    return {
        "schema_version": 1,
        "task_id": card["task_id"],
        "batch_id": card["batch_id"],
        "attempt_id": card["attempt_id"],
        "attempt_no": card["attempt_no"],
        "worker_instance_id": card["worker_instance_id"],
        "queue_sha256": card["queue_ref"]["sha256"],
        "queue_ordinal": card["queue_ordinal"],
        "task_card_sha256": contract.sha256_bytes(contract.canonical_json_bytes(card)),
        "owner_skill": card["owner_skill"],
        "agent_role": card["agent_role"],
        "status": "completed",
        "summary": "Reviewed one bounded packet.",
        "issues": [],
        "artifacts": [],
    }


def attempt_ledger(card: dict, *attempts: dict) -> dict:
    return {
        "schema_version": 1,
        "task_id": card["task_id"],
        "batch_id": card["batch_id"],
        "attempts": list(attempts),
    }


def ledger_entry(card: dict) -> dict:
    return {
        "attempt_id": card["attempt_id"],
        "attempt_no": card["attempt_no"],
        "worker_instance_id": card["worker_instance_id"],
        "queue_sha256": card["queue_ref"]["sha256"],
        "queue_ordinal": card["queue_ordinal"],
        "status": "failed",
    }


class ImportBatchContractTests(unittest.TestCase):
    def test_v1_contract_adapts_without_changing_legacy_validation(self) -> None:
        adapter = load_adapter()
        card = task_card()
        result = compact_result(card)
        original_card = json.loads(json.dumps(card))
        original_result = json.loads(json.dumps(result))

        adapted_card = adapter.adapt_task_card(card, campaign_id="legacy-import", scope_key="exercise-bank")
        adapted_result = adapter.adapt_compact_result(result, adapted_card)

        contract.validate_task_card(card)
        contract.validate_compact_result(result)
        contract.validate_result_against_task_card(result, card)
        self.assertEqual(card, original_card)
        self.assertEqual(result, original_result)
        self.assertEqual(adapted_card["schema_version"], 2)
        self.assertEqual(adapted_card["adapter"], "role-a-import-v1")
        self.assertEqual(adapted_card["role_plan"], ["role-a"])
        self.assertEqual(adapted_card["dispatch_attempt_no"], 1)
        self.assertEqual(adapted_result["schema_version"], 2)
        self.assertEqual(adapted_result["legacy_status"], "completed")
        self.assertEqual(adapted_result["task_card_sha256"], adapted_card["task_card_sha256"])

    def test_v1_adapter_cli_binds_exact_noncanonical_files_and_preserves_result(self) -> None:
        adapter = load_adapter()
        card = task_card()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            card_path = root / "legacy-card.json"
            adapted_card_path = root / "adapted-card.json"
            result_path = root / "legacy-result.json"
            adapted_result_path = root / "adapted-result.json"
            card_bytes = (json.dumps(card, ensure_ascii=False, indent=7) + "\r\n\r\n").encode("utf-8")
            card_path.write_bytes(card_bytes)
            result = compact_result(card)
            result.update(
                {
                    "task_card_sha256": contract.sha256_file(card_path),
                    "issues": [
                        {
                            "code": "legacy_warning",
                            "severity": "warning",
                            "message": "Preserve this issue.",
                        }
                    ],
                    "metrics": {"reviewed": 1, "unchanged": 0},
                    "next_action": "Run the legacy verifier.",
                }
            )
            result_path.write_bytes((json.dumps(result, ensure_ascii=False, indent=5) + "\r\n").encode("utf-8"))

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    adapter.main(
                        [
                            "task-card",
                            "--input",
                            str(card_path),
                            "--campaign",
                            "legacy-import",
                            "--scope-key",
                            "exercise-bank",
                            "--out",
                            str(adapted_card_path),
                            "--json",
                        ]
                    ),
                    0,
                )
            adapted_card = json.loads(adapted_card_path.read_text(encoding="utf-8"))
            self.assertEqual(adapted_card["task_card_sha256"], hashlib.sha256(card_bytes).hexdigest())
            self.assertEqual(adapted_card["legacy_task_card_ref"]["sha256"], hashlib.sha256(card_bytes).hexdigest())
            self.assertEqual(adapted_card["legacy_task_card_ref"]["size_bytes"], len(card_bytes))

            card_path.write_bytes(card_bytes + b" ")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    adapter.main(
                        [
                            "compact-result",
                            "--input",
                            str(result_path),
                            "--adapted-task-card",
                            str(adapted_card_path),
                            "--out",
                            str(adapted_result_path),
                            "--json",
                        ]
                    ),
                    2,
                )
            card_path.write_bytes(card_bytes)

            canonical_only = dict(result)
            canonical_only["task_card_sha256"] = contract.sha256_bytes(contract.canonical_json_bytes(card))
            result_path.write_text(json.dumps(canonical_only, ensure_ascii=False), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    adapter.main(
                        [
                            "compact-result",
                            "--input",
                            str(result_path),
                            "--adapted-task-card",
                            str(adapted_card_path),
                            "--out",
                            str(adapted_result_path),
                            "--json",
                        ]
                    ),
                    2,
                )
            result_path.write_bytes((json.dumps(result, ensure_ascii=False, indent=5) + "\r\n").encode("utf-8"))

            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    adapter.main(
                        [
                            "compact-result",
                            "--input",
                            str(result_path),
                            "--adapted-task-card",
                            str(adapted_card_path),
                            "--out",
                            str(adapted_result_path),
                            "--json",
                        ]
                    ),
                    0,
                )
            adapted_result = json.loads(adapted_result_path.read_text(encoding="utf-8"))
            self.assertEqual(adapted_result["legacy_compact_result"], result)
            self.assertEqual(adapted_result["issues"], result["issues"])
            self.assertEqual(adapted_result["metrics"], result["metrics"])
            self.assertEqual(adapted_result["next_action"], result["next_action"])
            self.assertEqual(adapted_result["legacy_compact_result_ref"]["sha256"], contract.sha256_file(result_path))

    def test_valid_task_and_result_are_hash_and_identity_bound(self) -> None:
        card = task_card()
        result = compact_result(card)

        contract.validate_task_card(card)
        contract.validate_compact_result(result)
        contract.validate_result_against_task_card(result, card)

        result["queue_ordinal"] = 1
        with self.assertRaisesRegex(contract.ContractError, "identity"):
            contract.validate_result_against_task_card(result, card)

    def test_queue_hash_and_ordinal_reordering_are_rejected(self) -> None:
        card = task_card()
        result = compact_result(card)
        for field, value in (("queue_sha256", "c" * 64), ("queue_ordinal", 2)):
            with self.subTest(field=field):
                changed = dict(result)
                changed[field] = value
                with self.assertRaises(contract.ContractError) as caught:
                    contract.validate_result_against_task_card(changed, card)
                self.assertEqual(caught.exception.code, "result_task_identity_mismatch")

    def test_task_card_enforces_all_profile_batch_limits(self) -> None:
        for field, value in (
            ("item_count", 6),
            ("page_count", 21),
            ("unresolved_review_blocks", 6),
        ):
            with self.subTest(field=field):
                card = task_card()
                card["batch_metrics"][field] = value
                with self.assertRaises(contract.ContractError) as caught:
                    contract.validate_task_card(card)
                self.assertEqual(caught.exception.code, "batch_limit_exceeded")

    def test_sensitive_reviewers_allow_only_explicitly_deidentified_packets(self) -> None:
        handwriting = task_card(
            "chinese-handwriting-formula-transcriber",
            "deidentified_abstain_summary",
        )
        handwriting["privacy"] = {"deidentified": True, "contains_raw_private_payload": False}
        contract.validate_task_card(handwriting)

        handwriting["privacy"]["deidentified"] = False
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_task_card(handwriting)
        self.assertEqual(caught.exception.code, "deidentification_required")

        student_data = task_card("student-data-import", "deidentified_kp_mapping")
        student_data["privacy"] = {"deidentified": True, "contains_raw_private_payload": False}
        contract.validate_task_card(student_data)

        student_data["payload_class"] = "student_row"
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_task_card(student_data)
        self.assertEqual(caught.exception.code, "payload_class_forbidden")

        student_data = task_card("student-data-import", "deidentified_kp_mapping")
        student_data["agent_role"] = "executor"
        student_data["privacy"] = {"deidentified": True, "contains_raw_private_payload": False}
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_task_card(student_data)
        self.assertEqual(caught.exception.code, "agent_role_forbidden")

    def test_raw_private_payload_assertion_is_always_rejected(self) -> None:
        card = task_card()
        card["privacy"] = {"deidentified": True, "contains_raw_private_payload": True}
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_task_card(card)
        self.assertEqual(caught.exception.code, "raw_private_payload_forbidden")

    def test_compact_result_uses_actual_file_size_limit(self) -> None:
        result = compact_result(task_card())
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_compact_result(result, encoded_size=16_385)
        self.assertEqual(caught.exception.code, "compact_result_too_large")

    def test_attempt_three_is_forbidden(self) -> None:
        card = task_card()
        card["attempt_no"] = 3
        card["retry_of"] = "attempt-002"
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_task_card(card)
        self.assertEqual(caught.exception.code, "attempt_no_invalid")

    def test_attempt_two_requires_prior_attempt_and_fresh_worker(self) -> None:
        first = task_card()
        second = task_card()
        second.update(
            {
                "attempt_id": "attempt-002",
                "attempt_no": 2,
                "retry_of": first["attempt_id"],
                "worker_instance_id": "worker-002",
            }
        )
        ledger = attempt_ledger(first, ledger_entry(first))
        contract.validate_task_card_against_attempt_ledger(second, ledger)

        second["worker_instance_id"] = first["worker_instance_id"]
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_task_card_against_attempt_ledger(second, ledger)
        self.assertEqual(caught.exception.code, "worker_instance_reused")

    def test_repeated_attempt_is_rejected_by_ledger(self) -> None:
        card = task_card()
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_task_card_against_attempt_ledger(card, attempt_ledger(card, ledger_entry(card)))
        self.assertEqual(caught.exception.code, "attempt_reused")

    def test_retry_rejects_ledger_queue_hash_or_ordinal_mismatch(self) -> None:
        first = task_card()
        second = task_card()
        second.update(
            {
                "attempt_id": "attempt-002",
                "attempt_no": 2,
                "retry_of": first["attempt_id"],
                "worker_instance_id": "worker-002",
            }
        )
        for field, value in (("queue_sha256", "c" * 64), ("queue_ordinal", 1)):
            with self.subTest(field=field):
                entry = ledger_entry(first)
                entry[field] = value
                with self.assertRaises(contract.ContractError) as caught:
                    contract.validate_task_card_against_attempt_ledger(second, attempt_ledger(first, entry))
                self.assertEqual(caught.exception.code, "ledger_queue_identity_mismatch")

    def test_result_task_identity_mismatch_is_rejected(self) -> None:
        card = task_card()
        result = compact_result(card)
        result["task_id"] = "different-task"
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_result_against_task_card(result, card)
        self.assertEqual(caught.exception.code, "result_task_identity_mismatch")

    def test_compact_result_rejects_embedded_markdown_or_private_bodies(self) -> None:
        result = compact_result(task_card())
        result["document_body"] = "# full markdown"
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_compact_result(result)
        self.assertEqual(caught.exception.code, "embedded_content_forbidden")

    def test_paths_reject_traversal_absolute_ads_and_noncanonical_forms(self) -> None:
        for relative_path in ("../escape.json", "/absolute.json", "C:/drive.json", "a/../b.json", "a//b.json", "a:stream"):
            with self.subTest(relative_path=relative_path):
                card = task_card()
                card["input_refs"][0]["relative_path"] = relative_path
                with self.assertRaises(contract.ContractError) as caught:
                    contract.validate_task_card(card)
                self.assertEqual(caught.exception.code, "relative_path_unsafe")

    def test_bundle_and_approval_require_attempt_identity(self) -> None:
        bundle = {
            "schema_version": 1,
            "task_id": "task-001",
            "batch_id": "batch-001",
            "attempt_id": "attempt-001",
            "owner_skill": "textbook-import",
            "artifacts": [
                {
                    "artifact_id": "artifact-1",
                    "source": {"path_id": "workspace.tmp", "relative_path": "stage/a.md"},
                    "target": {"path_id": "library.raw", "relative_path": "exercises/a.md"},
                    "sha256": SHA,
                    "size_bytes": 10,
                }
            ],
        }
        contract.validate_artifact_bundle(bundle)

        approval = {
            "schema_version": 1,
            "task_id": "task-001",
            "batch_id": "batch-001",
            "attempt_id": "attempt-001",
            "owner_skill": "exercise-bank-import",
            "bundle_sha256": SHA,
            "plan_sha256": "b" * 64,
            "approved": True,
            "approved_by": "main-agent",
            "approved_at": "2026-07-20T00:00:00Z",
        }
        contract.validate_artifact_approval(approval)

        del approval["attempt_id"]
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_artifact_approval(approval)
        self.assertEqual(caught.exception.code, "required_field_missing")

    def test_writer_stays_disabled_for_sensitive_generic_agent_profiles(self) -> None:
        bundle = {
            "schema_version": 1,
            "task_id": "task-001",
            "batch_id": "batch-001",
            "attempt_id": "attempt-001",
            "owner_skill": "student-data-import",
            "artifacts": [],
        }
        with self.assertRaises(contract.ContractError) as caught:
            contract.validate_artifact_bundle(bundle)
        self.assertEqual(caught.exception.code, "writer_disabled")

    def test_bemarkdown_and_exercise_require_canonical_writers(self) -> None:
        for owner_skill in ("bemarkdown", "exercise-bank-import"):
            with self.subTest(owner_skill=owner_skill):
                bundle = {
                    "schema_version": 1,
                    "task_id": "task-001",
                    "batch_id": "batch-001",
                    "attempt_id": "attempt-001",
                    "owner_skill": owner_skill,
                    "artifacts": [],
                }
                with self.assertRaises(contract.ContractError) as caught:
                    contract.validate_artifact_bundle(bundle)
                self.assertEqual(caught.exception.code, "canonical_writer_required")

    def test_cli_validation_binds_result_to_exact_task_card_bytes(self) -> None:
        card = task_card()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            card_path = root / "card.json"
            result_path = root / "result.json"
            ledger_path = root / "ledger.json"
            card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
            result = compact_result(card)
            result["task_card_sha256"] = contract.sha256_file(card_path)
            result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            ledger_path.write_text(json.dumps(attempt_ledger(card), ensure_ascii=False), encoding="utf-8")

            self.assertEqual(
                contract.main(
                    [
                        "validate-result",
                        "--input",
                        str(result_path),
                        "--task-card",
                        str(card_path),
                        "--attempt-ledger",
                        str(ledger_path),
                    ]
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
