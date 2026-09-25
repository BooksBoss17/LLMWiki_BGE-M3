from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "_shared/schemas"
SHA = "a" * 64


class AgentV3SchemaTests(unittest.TestCase):
    def validate(self, name: str, value: dict) -> None:
        schema = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(value)

    def test_four_small_control_plane_contracts(self) -> None:
        self.validate("campaign_state_v3.schema.json", {
            "schema_version": 3,
            "kind": "campaign_state",
            "campaign_id": "demo",
            "ledger_ref": {"path": "ledger.json", "sha256": SHA},
            "authorization_ref": {"path": "authorization.json", "sha256": SHA},
            "profile_ref": {"path": "profile.json", "sha256": SHA},
            "items": {"Q1": {"question_id": "Q1", "status": "queued"}},
            "active_run": None,
            "state_revision": 0,
        })
        self.validate("batch_card_v3.schema.json", {
            "schema_version": 3,
            "kind": "batch_card",
            "campaign_id": "demo",
            "batch_id": "batch-1",
            "run_id": "run-1",
            "phase": "executor",
            "stage_plan": [
                {"order": 1, "stage": "prepare_source", "primary_skill": "role-a", "completion": "source_slice_or_blocked", "formal_write": False},
                {"order": 2, "stage": "curate", "primary_skill": "role-d", "completion": "batch_result_submitted", "formal_write": False},
            ],
            "items": [{
                "question_id": "Q1",
                "target_ref": {"path_id": "project.root", "relative_path": "raw/Q1.md", "sha256": SHA},
                "capability": "complex",
                "source_cache_root": "cache/abc",
            }],
            "artifact_root": "artifacts/batch-1",
            "result_path": "artifacts/batch-1/result.json",
            "result_contract": {
                "skeleton_precreated": True,
                "must_submit_before_exit": True,
                "schema_path": "skills/_shared/schemas/batch_result_v3.schema.json",
                "producer_required": True,
            },
            "worker_contract": {
                "execution_mode": "isolated_worker",
                "fork_context": False,
                "worker_instance_id_source": "spawn_response",
                "producer_role": "executor",
                "main_agent_may_produce_result": False,
            },
        })
        self.validate("batch_result_v3.schema.json", {
            "schema_version": 3,
            "kind": "batch_result",
            "campaign_id": "demo",
            "batch_id": "batch-1",
            "run_id": "run-1",
            "phase": "executor",
            "batch_card_sha256": SHA,
            "status": "pending",
            "producer": None,
            "items": [{"question_id": "Q1", "status": "pending", "defects": []}],
        })
        self.validate("apply_receipt_v3.schema.json", {
            "schema_version": 3,
            "kind": "apply_receipt",
            "status": "applied",
            "campaign_id": "demo",
            "batch_id": "batch-1",
            "run_id": "run-2",
            "question_id": "Q1",
            "authorization_sha256": SHA,
            "files": [{"path": "raw/exercises/Q1.md", "before_sha256": SHA, "after_sha256": SHA}],
            "validations": {"ok": True},
        })

    def test_batch_card_rejects_implicit_multi_skill_execution(self) -> None:
        schema = json.loads((SCHEMAS / "batch_card_v3.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        legacy_card = {
            "schema_version": 3,
            "kind": "batch_card",
            "campaign_id": "demo",
            "batch_id": "batch-1",
            "run_id": "run-1",
            "phase": "executor",
            "skills": ["role-a", "role-d"],
            "items": [{"question_id": "Q1"}],
            "artifact_root": "artifacts/batch-1",
            "result_path": "artifacts/batch-1/result.json",
        }
        self.assertTrue(list(validator.iter_errors(legacy_card)))

    def test_batch_result_rejects_cache_only_item_without_status(self) -> None:
        schema = json.loads((SCHEMAS / "batch_result_v3.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        cache_only = {
            "schema_version": 3,
            "kind": "batch_result",
            "campaign_id": "demo",
            "batch_id": "batch-1",
            "run_id": "run-1",
            "phase": "executor",
            "batch_card_sha256": SHA,
            "items": [{"question_id": "Q1", "cache_path": "cache/full-source"}],
        }
        self.assertTrue(list(validator.iter_errors(cache_only)))

    def test_submitted_batch_result_requires_isolated_worker_producer(self) -> None:
        schema = json.loads((SCHEMAS / "batch_result_v3.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        submitted = {
            "schema_version": 3,
            "kind": "batch_result",
            "campaign_id": "demo",
            "batch_id": "batch-1",
            "run_id": "run-1",
            "phase": "executor",
            "batch_card_sha256": SHA,
            "status": "submitted",
            "items": [{"question_id": "Q1", "status": "runtime_failed", "defects": ["tool_failed"]}],
        }
        self.assertTrue(list(validator.iter_errors(submitted)))
        submitted["producer"] = {
            "worker_instance_id": "agent-1",
            "execution_mode": "main_agent_degraded",
            "role": "executor",
        }
        self.assertTrue(list(validator.iter_errors(submitted)))
        submitted["producer"] = {
            "worker_instance_id": "agent-1",
            "execution_mode": "isolated_worker",
            "role": "executor",
        }
        self.assertFalse(list(validator.iter_errors(submitted)))

    def test_source_mapped_result_requires_slice_and_evidence(self) -> None:
        schema = json.loads((SCHEMAS / "batch_result_v3.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        row = {
            "schema_version": 3,
            "kind": "batch_result",
            "campaign_id": "demo",
            "batch_id": "batch-1",
            "run_id": "run-1",
            "phase": "source",
            "batch_card_sha256": SHA,
            "status": "submitted",
            "producer": {
                "worker_instance_id": "source-agent",
                "execution_mode": "isolated_worker",
                "role": "source",
            },
            "items": [{"question_id": "Q1", "status": "source_mapped", "source_ref": {}}],
        }
        self.assertTrue(list(validator.iter_errors(row)))
        row["items"][0]["source_slice_ref"] = {"path": "slice.json", "sha256": SHA}
        row["items"][0]["evidence"] = {"path": "evidence.json", "sha256": SHA}
        self.assertFalse(list(validator.iter_errors(row)))

    def test_source_slice_contract_is_explicit_and_coordinate_bound(self) -> None:
        self.validate("source_slice_v3.schema.json", {
            "schema_version": 3,
            "kind": "source_slice",
            "question_id": "Q1",
            "source_sha256": SHA,
            "locator": {
                "type": "docx_paragraph_range",
                "coordinate_space": "word/document.xml:w:p:zero_based",
                "start": 10,
                "end": 20,
            },
            "artifact_ref": {"path": "Q1/source.md", "sha256": SHA},
        })

        schema = json.loads((SCHEMAS / "source_slice_v3.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        ambiguous = {
            "schema_version": 3,
            "kind": "source_slice",
            "question_id": "Q1",
            "source_sha256": SHA,
            "locator": {"type": "docx_paragraph_range", "start": 10, "end": 20},
            "artifact_ref": {"path": "Q1/source.md", "sha256": SHA},
        }
        self.assertTrue(list(validator.iter_errors(ambiguous)))

    def test_source_card_requires_the_explicit_slice_contract(self) -> None:
        schema = json.loads((SCHEMAS / "batch_card_v3.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        source_card = {
            "schema_version": 3,
            "kind": "batch_card",
            "campaign_id": "demo",
            "batch_id": "batch-source",
            "run_id": "run-source",
            "phase": "source",
            "stage_plan": [{
                "order": 1,
                "stage": "source_resolution",
                "primary_skill": "role-a",
                "completion": "batch_result_submitted",
                "formal_write": False,
            }],
            "items": [{
                "question_id": "Q1",
                "target_ref": {"path_id": "project.root", "relative_path": "raw/Q1.md", "sha256": SHA},
                "capability": "complex",
                "source_cache_root": "cache/source-sha",
                "source_slice_path": "cache/source-sha/Q1.slice.json",
                "source_artifact_root": "cache/source-sha/Q1",
            }],
            "artifact_root": "artifacts/run-source",
            "result_path": "results/run-source.json",
            "result_contract": {
                "skeleton_precreated": True,
                "must_submit_before_exit": True,
                "schema_path": "skills/_shared/schemas/batch_result_v3.schema.json",
                "producer_required": True,
            },
            "worker_contract": {
                "execution_mode": "isolated_worker",
                "fork_context": False,
                "worker_instance_id_source": "spawn_response",
                "producer_role": "source",
                "main_agent_may_produce_result": False,
            },
            "source_slice_contract": {
                "schema_path": "skills/_shared/schemas/source_slice_v3.schema.json",
                "manifest_skeleton_precreated": True,
                "worker_must_overwrite_skeleton": True,
                "artifact_must_be_under_item_root": True,
                "locator_coordinate_space_required": True,
            },
        }
        self.assertFalse(list(validator.iter_errors(source_card)))
        source_card.pop("source_slice_contract")
        self.assertTrue(list(validator.iter_errors(source_card)))


if __name__ == "__main__":
    unittest.main()
