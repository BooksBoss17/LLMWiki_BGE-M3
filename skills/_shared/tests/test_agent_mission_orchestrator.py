from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "skills/_shared/scripts/agent_mission_orchestrator.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


FAKE_WRITER = r'''
import argparse, hashlib, json
from pathlib import Path

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
p=argparse.ArgumentParser()
for name in ("proposal","audit","authorization","receipt","campaign","batch","run","question"):
    p.add_argument("--"+name, required=True)
a=p.parse_args()
proposal=json.loads(Path(a.proposal).read_text(encoding="utf-8"))
audit=json.loads(Path(a.audit).read_text(encoding="utf-8"))
auth=json.loads(Path(a.authorization).read_text(encoding="utf-8"))
target=Path(proposal["target_path"])
before=sha(target)
if before != proposal["expected_sha256"]:
    raise SystemExit(4)
row=next(x for x in audit["items"] if x["question_id"] == a.question)
if row["verdict"] != "passed": raise SystemExit(5)
backup=Path(a.receipt).with_suffix(".backup")
backup.parent.mkdir(parents=True, exist_ok=True)
backup.write_bytes(target.read_bytes())
target.write_text(proposal["content"], encoding="utf-8")
after=sha(target)
receipt={
 "schema_version":3,"kind":"apply_receipt","status":"applied",
 "campaign_id":a.campaign,"batch_id":a.batch,"run_id":a.run,
 "question_id":a.question,"authorization_sha256":sha(Path(a.authorization)),
 "files":[{"path":str(target.resolve()),"before_sha256":before,"after_sha256":after,"backup_path":str(backup)}],
 "validations":{"ok":True}
}
Path(a.receipt).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
'''


class CampaignFixture:
    def __init__(
        self,
        root: Path,
        *,
        campaign: str = "fixture",
        count: int = 5,
        profile_id: str = "exercise",
        path_prefix: str = "",
        capability: str = "bounded",
        capabilities: list[str] | None = None,
        source_groups: list[int] | None = None,
        canary: bool = False,
        initial_statuses: list[str] | None = None,
        prealigned: bool = True,
    ) -> None:
        self.root = root
        self.campaign = campaign
        self.prealigned = prealigned
        self.writer = root / "fake_writer.py"
        self.writer.write_text(FAKE_WRITER, encoding="utf-8")
        items = []
        for index in range(count):
            target = root / "raw" / path_prefix / f"Q{index}.md"
            source_group = source_groups[index] if source_groups else 0
            source = root / "source" / f"S{source_group}.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            source.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"old-{index}", encoding="utf-8")
            source.write_text(f"source-{source_group}", encoding="utf-8")
            initial_status = initial_statuses[index] if initial_statuses else "queued"
            items.append({
                "question_id": f"Q{index}", "queue_ordinal": index, "severity": "P0",
                "source_ready": True,
                "capability": capabilities[index] if capabilities else capability,
                "initial_status": initial_status,
                "target_ref": {
                    "path_id": "project.root",
                    "relative_path": target.relative_to(root).as_posix(),
                    "sha256": digest(target),
                },
                "source_ref": {"path_id": "project.root", "relative_path": f"source/S{source_group}.txt", "sha256": digest(source)},
            })
        self.ledger = root / f"{campaign}.ledger.json"
        write_json(self.ledger, {"schema_version": 3, "campaign_id": campaign, "scope_key": campaign, "items": items})
        self.authorization = root / f"{campaign}.authorization.json"
        authorization_record = root / f"{campaign}.user-authorization.json"
        write_json(authorization_record, {"authorized": True})
        write_json(self.authorization, {
            "schema_version": 3, "campaign_id": campaign, "ledger_sha256": digest(self.ledger),
            "destructive": False, "expires_at": "2099-01-01T00:00:00Z",
            "writer": "fake_writer.py", "allowed_fields": ["content"],
            "authorization_record_path": str(authorization_record),
            "authorization_record_sha256": digest(authorization_record),
        })
        self.profile = root / f"{campaign}.profile.json"
        profile = {
            "schema_version": 3, "kind": "campaign_profile", "profile_id": profile_id,
            "executor_skills": ["role-a", "role-d"], "auditor_skills": ["role-d", "role-c"],
            "allowed_paths": ["raw"],
            "model_policy": {
                "bounded": "test-model", "ambiguous": "test-model",
                "complex": "test-model", "critical": "test-model",
            },
            "writer": {
                "mode": "python", "script": "fake_writer.py", "timeout_seconds": 30,
                "arguments": [
                    "--proposal", "{proposal_path}", "--audit", "{audit_result_path}",
                    "--authorization", "{authorization_path}", "--receipt", "{receipt_path}",
                    "--campaign", "{campaign_id}", "--batch", "{batch_id}",
                    "--run", "{run_id}", "--question", "{question_id}",
                ],
            },
        }
        if canary:
            profile["rollout_policy"] = {
                "canary_campaigns": [campaign],
                "required_complete_batches_each": 1,
                "max_parallel_campaigns_after_canary": 4,
            }
        write_json(self.profile, profile)

    @property
    def state_root(self) -> Path:
        return self.root / "skills/_ops/runtime/state/agent_batches_v3" / self.campaign

    def command(self, *args: str, check: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--kb-root", str(self.root), *args, "--json"],
            text=True, encoding="utf-8", capture_output=True, check=False,
        )
        payload = json.loads(completed.stdout)
        if check and completed.returncode:
            raise AssertionError(payload)
        return payload

    def init(self) -> dict:
        payload = self.command("init", "--campaign", self.campaign, "--ledger", str(self.ledger),
                               "--authorization", str(self.authorization), "--profile", str(self.profile))
        if self.prealigned:
            state = self.state()
            for item in state["items"].values():
                if item["status"] not in {"queued", "revalidation_queued"} or item.get("source_ref") is None:
                    continue
                source_slice = self.source_slice(item["question_id"])
                item["source_slice_ref"] = {"path": str(source_slice), "sha256": digest(source_slice)}
            write_json(self.state_root / "campaign.json", state)
        return payload

    def dispatch(self) -> tuple[dict, dict]:
        payload = self.command("dispatch", "--campaign", self.campaign)
        card = json.loads(Path(payload["batch_card"]["path"]).read_text(encoding="utf-8"))
        return payload, card

    def state(self) -> dict:
        return json.loads((self.state_root / "campaign.json").read_text(encoding="utf-8"))

    def source_slice(
        self,
        question_id: str,
        *,
        manifest_path: Path | None = None,
        artifact_root: Path | None = None,
    ) -> Path:
        item = self.state()["items"][question_id]
        source_sha = item["source_ref"]["sha256"]
        cache_root = self.state_root / "source-cache" / source_sha
        excerpt = (artifact_root / "source.md") if artifact_root else (cache_root / f"{question_id}.md")
        excerpt.parent.mkdir(parents=True, exist_ok=True)
        excerpt.write_text(f"verified source slice for {question_id}", encoding="utf-8")
        manifest = manifest_path or (cache_root / f"{question_id}.source-slice.json")
        write_json(manifest, {
            "schema_version": 3,
            "kind": "source_slice",
            "question_id": question_id,
            "source_sha256": source_sha,
            "locator": {
                "type": "verified_excerpt",
                "coordinate_space": "utf8:whole_artifact",
                "label": question_id,
            },
            "artifact_ref": {"path": str(excerpt), "sha256": digest(excerpt)},
        })
        return manifest

    def executor_result(
        self,
        card: dict,
        *,
        no_change: set[str] | None = None,
        runtime_failed: set[str] | None = None,
        worker_instance_id: str | None = None,
    ) -> Path:
        no_change = no_change or set()
        runtime_failed = runtime_failed or set()
        artifact_root = Path(card["artifact_root"])
        rows = []
        for item in card["items"]:
            qid = item["question_id"]
            if qid in runtime_failed:
                rows.append({
                    "question_id": qid,
                    "status": "runtime_failed",
                    "defects": ["source_conversion_interrupted"],
                })
                continue
            evidence = artifact_root / qid / "evidence.json"
            write_json(evidence, {"question_id": qid, "ok": True})
            source_slice_ref = item.get("source_slice_ref")
            if source_slice_ref is None:
                source_slice = self.source_slice(qid)
                source_slice_ref = {"path": str(source_slice), "sha256": digest(source_slice)}
            if qid in no_change:
                rows.append({
                    "question_id": qid,
                    "status": "proposed",
                    "intent": "no_change",
                    "source_slice_ref": source_slice_ref,
                    "evidence": {"path": str(evidence), "sha256": digest(evidence)},
                })
                continue
            proposal = artifact_root / qid / "proposal.json"
            dry = artifact_root / qid / "dry-run.json"
            target = self.root / item["target_ref"]["relative_path"]
            write_json(proposal, {"question_id": qid, "target_path": str(target.resolve()), "expected_sha256": item["target_ref"]["sha256"], "content": f"new-{qid}"})
            write_json(dry, {"question_id": qid, "ok": True, "proposal_sha256": digest(proposal)})
            rows.append({
                "question_id": qid, "status": "proposed", "intent": "apply",
                "source_slice_ref": source_slice_ref,
                "proposal": {"path": str(proposal), "sha256": digest(proposal)},
                "evidence": {"path": str(evidence), "sha256": digest(evidence)},
                "dry_run": {"path": str(dry), "sha256": digest(dry)},
            })
        result = Path(card["result_path"])
        write_json(result, {
            "schema_version": 3, "kind": "batch_result", "campaign_id": card["campaign_id"],
            "batch_id": card["batch_id"], "run_id": card["run_id"], "phase": card["phase"],
            "batch_card_sha256": digest(self.state_root / "cards" / f"{card['run_id']}.json"),
            "status": "submitted",
            "producer": {
                "worker_instance_id": worker_instance_id or f"worker-{card['run_id']}",
                "execution_mode": "isolated_worker",
                "role": card["phase"],
            },
            "items": rows,
        })
        return result

    def source_result(self, card: dict, *, worker_instance_id: str | None = None) -> Path:
        artifact_root = Path(card["artifact_root"])
        rows = []
        state = self.state()
        for item in card["items"]:
            qid = item["question_id"]
            evidence = artifact_root / qid / "source-evidence.json"
            write_json(evidence, {"question_id": qid, "source_aligned": True})
            source_slice = self.source_slice(
                qid,
                manifest_path=Path(item["source_slice_path"]),
                artifact_root=Path(item["source_artifact_root"]),
            )
            rows.append({
                "question_id": qid,
                "status": "source_mapped",
                "source_ref": item.get("source_ref") or state["items"][qid]["source_ref"],
                "source_slice_ref": {"path": str(source_slice), "sha256": digest(source_slice)},
                "evidence": {"path": str(evidence), "sha256": digest(evidence)},
                "defects": [],
            })
        result = Path(card["result_path"])
        write_json(result, {
            "schema_version": 3, "kind": "batch_result", "campaign_id": card["campaign_id"],
            "batch_id": card["batch_id"], "run_id": card["run_id"], "phase": card["phase"],
            "batch_card_sha256": digest(self.state_root / "cards" / f"{card['run_id']}.json"),
            "status": "submitted",
            "producer": {
                "worker_instance_id": worker_instance_id or f"worker-{card['run_id']}",
                "execution_mode": "isolated_worker",
                "role": card["phase"],
            },
            "items": rows,
        })
        return result

    def audit_result(
        self,
        card: dict,
        *,
        failed: set[str] | None = None,
        worker_instance_id: str | None = None,
    ) -> Path:
        failed = failed or set()
        artifact_root = Path(card["artifact_root"])
        rows = []
        for item in card["items"]:
            qid = item["question_id"]
            audit = artifact_root / qid / "audit.json"
            verdict = "failed" if qid in failed else "passed"
            defects = ["content_mismatch"] if verdict == "failed" else []
            write_json(audit, {"question_id": qid, "verdict": verdict, "defects": defects})
            rows.append({"question_id": qid, "verdict": verdict, "defects": defects, "audit": {"path": str(audit), "sha256": digest(audit)}})
        result = Path(card["result_path"])
        write_json(result, {
            "schema_version": 3, "kind": "batch_result", "campaign_id": card["campaign_id"],
            "batch_id": card["batch_id"], "run_id": card["run_id"], "phase": card["phase"],
            "batch_card_sha256": digest(self.state_root / "cards" / f"{card['run_id']}.json"),
            "status": "submitted",
            "producer": {
                "worker_instance_id": worker_instance_id or f"worker-{card['run_id']}",
                "execution_mode": "isolated_worker",
                "role": card["phase"],
            },
            "items": rows,
        })
        return result


class AgentBatchV3Tests(unittest.TestCase):
    def test_public_interface_has_five_commands(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual({"init", "dispatch", "submit", "recover", "status"}, set(__import__("re").findall(r'commands\.add_parser\("([^"]+)"\)', text)))
        for legacy in ("attestation", "target_lock", "cutover", "legacy_execution_adoption", "lease_id"):
            self.assertNotIn(legacy, text)

    def test_five_items_stage_audit_then_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp)); f.init()
            _, executor = f.dispatch()
            f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(executor)))
            self.assertEqual({"audit_queued": 5}, f.command("status", "--campaign", f.campaign)["counts"])
            _, auditor = f.dispatch()
            result = f.audit_result(auditor)
            submitted = f.command("submit", "--campaign", f.campaign, "--result", str(result))
            self.assertEqual({"passed": 5}, submitted["counts"])
            self.assertTrue(all("apply_receipt" in item for item in submitted["items"]))
            self.assertTrue(all((f.root / "raw" / f"Q{i}.md").read_text(encoding="utf-8") == f"new-Q{i}" for i in range(5)))

    def test_submit_rejects_missing_or_degraded_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            _, card = f.dispatch()
            result_path = f.executor_result(card)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result.pop("producer")
            write_json(result_path, result)
            rejected = f.command("submit", "--campaign", f.campaign, "--result", str(result_path), check=False)
            self.assertEqual("producer_required", rejected["error"])

            result["producer"] = {
                "worker_instance_id": "main-agent",
                "execution_mode": "main_agent_degraded",
                "role": "executor",
            }
            write_json(result_path, result)
            rejected = f.command("submit", "--campaign", f.campaign, "--result", str(result_path), check=False)
            self.assertEqual("producer_execution_mode_invalid", rejected["error"])

    def test_auditor_must_be_a_fresh_isolated_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            _, executor = f.dispatch()
            worker_id = "worker-shared"
            f.command(
                "submit", "--campaign", f.campaign, "--result",
                str(f.executor_result(executor, worker_instance_id=worker_id)),
            )
            _, auditor = f.dispatch()
            rejected = f.command(
                "submit", "--campaign", f.campaign, "--result",
                str(f.audit_result(auditor, worker_instance_id=worker_id)),
                check=False,
            )
            self.assertEqual("producer_identity_collision", rejected["error"])
            self.assertEqual({"auditor_running": 1}, f.command("status", "--campaign", f.campaign)["counts"])

    def test_passed_item_can_enter_controlled_revalidation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            _, executor = f.dispatch()
            f.command(
                "submit", "--campaign", f.campaign, "--result",
                str(f.executor_result(executor, worker_instance_id="worker-executor")),
            )
            _, auditor = f.dispatch()
            f.command(
                "submit", "--campaign", f.campaign, "--result",
                str(f.audit_result(auditor, worker_instance_id="worker-auditor")),
            )
            self.assertEqual({"passed": 1}, f.command("status", "--campaign", f.campaign)["counts"])
            state = f.state()
            state["items"]["Q0"]["dispatch_attempts"]["executor:r0"] = 2
            write_json(f.state_root / "campaign.json", state)
            recovered = f.command(
                "recover", "--campaign", f.campaign,
                "--revalidate-passed", "Q0",
                "--reason", "historical_auditor_identity_unverified",
            )
            self.assertEqual("Q0", recovered["revalidation_queued"])
            self.assertEqual({"revalidation_queued": 1}, recovered["counts"])
            _, card = f.dispatch()
            self.assertEqual("executor", card["phase"])
            self.assertEqual(["Q0"], [item["question_id"] for item in card["items"]])

    def test_runtime_retry_rejects_reused_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            worker_id = "worker-reused"
            _, first = f.dispatch()
            f.command(
                "submit", "--campaign", f.campaign, "--result",
                str(f.executor_result(first, runtime_failed={"Q0"}, worker_instance_id=worker_id)),
            )
            _, second = f.dispatch()
            rejected = f.command(
                "submit", "--campaign", f.campaign, "--result",
                str(f.executor_result(second, worker_instance_id=worker_id)),
                check=False,
            )
            self.assertEqual("producer_reused", rejected["error"])

    def test_complex_canary_dispatches_one_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(
                Path(tmp),
                campaign="canary-optics",
                count=5,
                capability="complex",
                canary=True,
            )
            f.init()
            _, card = f.dispatch()
            self.assertEqual(1, len(card["items"]))
            self.assertEqual("Q0", card["items"][0]["question_id"])

    def test_source_blocked_dispatches_source_phase_before_queued_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(
                Path(tmp),
                count=2,
                initial_statuses=["source_blocked", "queued"],
            )
            f.init()
            _, card = f.dispatch()
            self.assertEqual("source", card["phase"])
            self.assertEqual(["Q0"], [item["question_id"] for item in card["items"]])

    def test_dispatch_precreates_result_skeleton_and_explicit_stage_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1)
            f.init()
            _, card = f.dispatch()

            self.assertNotIn("skills", card)
            self.assertEqual(
                ["role-d"],
                [stage["primary_skill"] for stage in card["stage_plan"]],
            )
            self.assertEqual("batch_result_submitted", card["stage_plan"][-1]["completion"])
            self.assertEqual("executor", card["worker_contract"]["producer_role"])

            skeleton_path = Path(card["result_path"])
            self.assertTrue(skeleton_path.is_file())
            skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
            self.assertEqual("pending", skeleton["items"][0]["status"])
            self.assertIsNone(skeleton["items"][0]["intent"])
            self.assertIsNone(skeleton["items"][0]["evidence"])
            self.assertIsNone(skeleton["items"][0]["proposal"])
            self.assertIsNone(skeleton["items"][0]["dry_run"])
            self.assertEqual(card["run_id"], skeleton["run_id"])
            self.assertEqual(digest(f.state_root / "cards" / f"{card['run_id']}.json"), skeleton["batch_card_sha256"])
            self.assertEqual(
                "skills/_shared/schemas/batch_result_v3.schema.json",
                card["result_contract"]["schema_path"],
            )

            source_cache_root = Path(card["items"][0]["source_cache_root"])
            source_sha = card["items"][0]["source_ref"]["sha256"]
            self.assertEqual(source_sha, source_cache_root.name)

    def test_source_alignment_wave_precedes_curation_and_batches_complex_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(
                Path(tmp),
                campaign="canary-alignment",
                count=5,
                capability="complex",
                canary=True,
                prealigned=False,
            )
            f.init()
            _, source_card = f.dispatch()
            self.assertEqual("source", source_card["phase"])
            self.assertEqual(5, len(source_card["items"]))
            self.assertEqual("source", source_card["worker_contract"]["producer_role"])
            self.assertEqual(["role-a"], [stage["primary_skill"] for stage in source_card["stage_plan"]])
            self.assertEqual(
                "skills/_shared/schemas/source_slice_v3.schema.json",
                source_card["source_slice_contract"]["schema_path"],
            )
            self.assertTrue(source_card["source_slice_contract"]["manifest_skeleton_precreated"])
            for item in source_card["items"]:
                manifest_path = Path(item["source_slice_path"])
                self.assertTrue(manifest_path.is_file())
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual("source_slice", manifest["kind"])
                self.assertEqual(item["question_id"], manifest["question_id"])
                self.assertEqual(item["source_ref"]["sha256"], manifest["source_sha256"])
                self.assertIsNone(manifest["locator"])
                self.assertIsNone(manifest["artifact_ref"])
                self.assertTrue(Path(item["source_artifact_root"]).is_relative_to(Path(item["source_cache_root"])))
            source_skeleton = json.loads(Path(source_card["result_path"]).read_text(encoding="utf-8"))
            self.assertIsNone(source_skeleton["items"][0]["source_slice_ref"])

            f.command(
                "submit", "--campaign", f.campaign, "--result", str(f.source_result(source_card)),
            )
            self.assertEqual({"source_audit_queued": 5}, f.command("status", "--campaign", f.campaign)["counts"])
            _, auditor_card = f.dispatch()
            f.command(
                "submit", "--campaign", f.campaign, "--result", str(f.audit_result(auditor_card)),
            )
            state = f.state()
            self.assertTrue(all(item["status"] == "queued" for item in state["items"].values()))
            self.assertTrue(all(item["source_slice_ref"] is not None for item in state["items"].values()))

            _, executor_card = f.dispatch()
            self.assertEqual("executor", executor_card["phase"])
            self.assertEqual(1, len(executor_card["items"]))
            self.assertEqual(["role-d"], [stage["primary_skill"] for stage in executor_card["stage_plan"]])

    def test_source_retry_replaces_stale_audit_and_defects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1, prealigned=False)
            f.init()

            _, first_source = f.dispatch()
            f.command(
                "submit", "--campaign", f.campaign,
                "--result", str(f.source_result(first_source, worker_instance_id="source-one")),
            )
            _, first_auditor = f.dispatch()
            f.command(
                "submit", "--campaign", f.campaign,
                "--result", str(f.audit_result(first_auditor, failed={"Q0"}, worker_instance_id="auditor-one")),
            )
            failed_state = f.state()["items"]["Q0"]
            self.assertEqual("source_blocked", failed_state["status"])
            self.assertIsNotNone(failed_state["audit"])
            self.assertTrue(failed_state["defects"])

            _, second_source = f.dispatch()
            submitted = f.command(
                "submit", "--campaign", f.campaign,
                "--result", str(f.source_result(second_source, worker_instance_id="source-two")),
            )
            self.assertEqual("source_audit_queued", submitted["items"][0]["status"])
            self.assertNotIn("defects", submitted["items"][0])

            retried_state = f.state()["items"]["Q0"]
            self.assertIsNone(retried_state["audit"])
            self.assertEqual([], retried_state["defects"])
            self.assertEqual("source-two", retried_state["candidate"]["producer"]["worker_instance_id"])

            _, second_auditor = f.dispatch()
            self.assertIsNone(second_auditor["items"][0]["prior_audit"])

    def test_source_submit_requires_registered_manifest_and_coordinate_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1, prealigned=False)
            f.init()
            _, source_card = f.dispatch()
            result_path = f.source_result(source_card, worker_instance_id="source-one")
            result = json.loads(result_path.read_text(encoding="utf-8"))
            expected_manifest = Path(source_card["items"][0]["source_slice_path"])

            alternate_manifest = expected_manifest.with_name("alternate-source-slice.json")
            alternate_manifest.write_bytes(expected_manifest.read_bytes())
            result["items"][0]["source_slice_ref"] = {
                "path": str(alternate_manifest),
                "sha256": digest(alternate_manifest),
            }
            write_json(result_path, result)
            rejected = f.command(
                "submit", "--campaign", f.campaign, "--result", str(result_path), check=False,
            )
            self.assertEqual("source_slice_path_mismatch", rejected["error"])

            manifest = json.loads(expected_manifest.read_text(encoding="utf-8"))
            coordinate_space = manifest["locator"].pop("coordinate_space")
            write_json(expected_manifest, manifest)
            result["items"][0]["source_slice_ref"] = {
                "path": str(expected_manifest),
                "sha256": digest(expected_manifest),
            }
            write_json(result_path, result)
            rejected = f.command(
                "submit", "--campaign", f.campaign, "--result", str(result_path), check=False,
            )
            self.assertEqual("source_slice_coordinate_space_missing", rejected["error"])

            manifest["locator"]["coordinate_space"] = coordinate_space
            write_json(expected_manifest, manifest)
            result["items"][0]["source_slice_ref"]["sha256"] = digest(expected_manifest)
            write_json(result_path, result)
            submitted = f.command(
                "submit", "--campaign", f.campaign, "--result", str(result_path),
            )
            self.assertEqual("source_audit_queued", submitted["items"][0]["status"])

    def test_proposed_result_persists_slice_and_missing_slice_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1)
            f.init()
            _, card = f.dispatch()
            result_path = f.executor_result(card)
            result = json.loads(result_path.read_text(encoding="utf-8"))
            source_slice_ref = result["items"][0].pop("source_slice_ref")
            write_json(result_path, result)
            rejected = f.command(
                "submit", "--campaign", f.campaign, "--result", str(result_path), check=False,
            )
            self.assertEqual("source_slice_required_for_proposal", rejected["error"])
            self.assertEqual("old-0", (f.root / "raw/Q0.md").read_text(encoding="utf-8"))

            result["items"][0]["source_slice_ref"] = source_slice_ref
            write_json(result_path, result)
            submitted = f.command("submit", "--campaign", f.campaign, "--result", str(result_path))
            persisted = f.state()["items"]["Q0"]["source_slice_ref"]
            self.assertEqual(source_slice_ref["sha256"], persisted["sha256"])
            self.assertEqual(Path(source_slice_ref["path"]).resolve(), Path(persisted["path"]).resolve())
            self.assertEqual("audit_queued", submitted["items"][0]["status"])
            self.assertEqual(persisted["sha256"], submitted["items"][0]["source_slice_ref"]["sha256"])

    def test_dispatch_groups_by_source_and_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(
                Path(tmp),
                count=5,
                source_groups=[0, 0, 1, 0, 0],
                capabilities=["bounded", "bounded", "bounded", "ambiguous", "bounded"],
            )
            f.init()
            _, card = f.dispatch()
            self.assertEqual(["Q0", "Q1", "Q4"], [item["question_id"] for item in card["items"]])
            self.assertEqual(1, len({item["source_ref"]["sha256"] for item in card["items"]}))
            self.assertEqual({"bounded"}, {item["capability"] for item in card["items"]})

    def test_same_source_and_capability_fill_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=5)
            f.init()
            _, card = f.dispatch()
            self.assertEqual(5, len(card["items"]))

    def test_submit_rejects_untouched_result_skeleton(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1)
            f.init()
            _, card = f.dispatch()
            rejected = f.command(
                "submit",
                "--campaign",
                f.campaign,
                "--result",
                card["result_path"],
                check=False,
            )
            self.assertEqual("result_not_submitted", rejected["error"])

    def test_structured_runtime_failure_requeues_once_then_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1)
            f.init()
            _, first = f.dispatch()
            f.command(
                "submit",
                "--campaign",
                f.campaign,
                "--result",
                str(f.executor_result(first, runtime_failed={"Q0"})),
            )
            self.assertEqual({"queued": 1}, f.command("status", "--campaign", f.campaign)["counts"])

            _, second = f.dispatch()
            f.command(
                "submit",
                "--campaign",
                f.campaign,
                "--result",
                str(f.executor_result(second, runtime_failed={"Q0"})),
            )
            state = f.state()["items"]["Q0"]
            self.assertEqual("blocked", state["status"])
            self.assertEqual(["runtime_attempts_exhausted"], state["defects"])

    def test_recover_requeues_runtime_blocked_item_only_after_source_slice_added(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1)
            f.init()
            _, first = f.dispatch()
            f.command(
                "recover",
                "--campaign",
                f.campaign,
                "--run-id",
                first["run_id"],
                "--reason",
                "worker_interrupted",
            )
            _, second = f.dispatch()
            f.command(
                "recover",
                "--campaign",
                f.campaign,
                "--run-id",
                second["run_id"],
                "--reason",
                "worker_interrupted_again",
            )
            self.assertEqual({"blocked": 1}, f.command("status", "--campaign", f.campaign)["counts"])

            missing_slice = f.command(
                "recover",
                "--campaign",
                f.campaign,
                "--requeue-blocked",
                "Q0",
                "--reason",
                "input_refined",
                check=False,
            )
            self.assertEqual("source_slice_required", missing_slice["error"])

            source_slice = f.source_slice("Q0")
            recovered = f.command(
                "recover",
                "--campaign",
                f.campaign,
                "--requeue-blocked",
                "Q0",
                "--source-slice",
                str(source_slice),
                "--reason",
                "verified_question_slice_added",
            )
            self.assertEqual({"queued": 1}, recovered["counts"])
            _, third = f.dispatch()
            self.assertNotEqual(second["run_id"], third["run_id"])
            self.assertEqual(digest(source_slice), third["items"][0]["source_slice_ref"]["sha256"])

    def test_recover_requeues_explicitly_interrupted_executor_with_source_slice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1)
            f.init()
            _, card = f.dispatch()
            artifact = Path(card["artifact_root"]) / "Q0" / "evidence.json"
            write_json(artifact, {"question_id": "Q0", "ok": False})
            result = Path(card["result_path"])
            write_json(result, {
                "schema_version": 3,
                "kind": "batch_result",
                "campaign_id": card["campaign_id"],
                "batch_id": card["batch_id"],
                "run_id": card["run_id"],
                "phase": "executor",
                "batch_card_sha256": digest(f.state_root / "cards" / f"{card['run_id']}.json"),
                "status": "submitted",
                "producer": {
                    "worker_instance_id": f"worker-{card['run_id']}",
                    "execution_mode": "isolated_worker",
                    "role": "executor",
                },
                "items": [{
                    "question_id": "Q0",
                    "status": "unresolved",
                    "defects": [
                        "media_formula_evidence_not_fully_reverified",
                        "executor_stopped_before_complete_review",
                    ],
                    "evidence": {"path": str(artifact), "sha256": digest(artifact)},
                }],
            })
            f.command("submit", "--campaign", f.campaign, "--result", str(result))
            self.assertEqual({"blocked": 1}, f.command("status", "--campaign", f.campaign)["counts"])
            source_slice = f.source_slice("Q0")
            recovered = f.command(
                "recover", "--campaign", f.campaign, "--requeue-blocked", "Q0",
                "--source-slice", str(source_slice), "--reason", "retry_interrupted_executor",
            )
            self.assertEqual({"queued": 1}, recovered["counts"])

    def test_recover_still_rejects_generic_unresolved_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1)
            f.init()
            _, card = f.dispatch()
            artifact = Path(card["artifact_root"]) / "Q0" / "evidence.json"
            write_json(artifact, {"question_id": "Q0", "ok": False})
            result = Path(card["result_path"])
            write_json(result, {
                "schema_version": 3,
                "kind": "batch_result",
                "campaign_id": card["campaign_id"],
                "batch_id": card["batch_id"],
                "run_id": card["run_id"],
                "phase": "executor",
                "batch_card_sha256": digest(f.state_root / "cards" / f"{card['run_id']}.json"),
                "status": "submitted",
                "producer": {
                    "worker_instance_id": f"worker-{card['run_id']}",
                    "execution_mode": "isolated_worker",
                    "role": "executor",
                },
                "items": [{
                    "question_id": "Q0", "status": "unresolved", "defects": ["content_unresolved"],
                    "evidence": {"path": str(artifact), "sha256": digest(artifact)},
                }],
            })
            f.command("submit", "--campaign", f.campaign, "--result", str(result))
            source_slice = f.source_slice("Q0")
            rejected = f.command(
                "recover", "--campaign", f.campaign, "--requeue-blocked", "Q0",
                "--source-slice", str(source_slice), "--reason", "must_reject_content_unresolved", check=False,
            )
            self.assertEqual("blocked_item_not_recoverable", rejected["error"])

    def test_partial_pass_applies_four_and_repairs_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp)); f.init()
            _, card = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(card)))
            persisted_slice = f.state()["items"]["Q4"]["source_slice_ref"]
            self.assertIsNotNone(persisted_slice)
            _, card = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(card, failed={"Q4"})))
            self.assertEqual({"passed": 4, "repair_queued": 1}, f.command("status", "--campaign", f.campaign)["counts"])
            _, card = f.dispatch(); self.assertEqual("repair", card["phase"])
            self.assertEqual("repair", card["worker_contract"]["producer_role"])
            self.assertEqual(["role-d"], [stage["primary_skill"] for stage in card["stage_plan"]])
            self.assertEqual(persisted_slice, card["items"][0]["source_slice_ref"])
            f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(card)))
            _, card = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(card)))
            self.assertEqual({"passed": 5}, f.command("status", "--campaign", f.campaign)["counts"])

    def test_second_audit_failure_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(c)))
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(c, failed={"Q0"})))
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(c)))
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(c, failed={"Q0"})))
            self.assertEqual({"blocked": 1}, f.command("status", "--campaign", f.campaign)["counts"])

    def test_writer_failure_gets_one_repair_then_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            f.writer.write_text("raise SystemExit(9)\n", encoding="utf-8")
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(c)))
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(c)))
            self.assertEqual({"repair_queued": 1}, f.command("status", "--campaign", f.campaign)["counts"])
            _, c = f.dispatch(); self.assertEqual("repair", c["phase"])
            f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(c)))
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(c)))
            self.assertEqual({"blocked": 1}, f.command("status", "--campaign", f.campaign)["counts"])

    def test_no_change_requires_audit_but_no_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(c, no_change={"Q0"})))
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(c)))
            self.assertEqual({"no_change_passed": 1}, f.command("status", "--campaign", f.campaign)["counts"])
            self.assertEqual("old-0", (f.root / "raw/Q0.md").read_text(encoding="utf-8"))

    def test_late_result_is_rejected_after_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            _, old = f.dispatch(); late = f.executor_result(old)
            f.command("recover", "--campaign", f.campaign, "--run-id", old["run_id"], "--reason", "worker_lost")
            _, new = f.dispatch()
            rejected = f.command("submit", "--campaign", f.campaign, "--result", str(late), check=False)
            self.assertFalse(rejected["ok"])
            self.assertIn(rejected["error"], {"result_path_mismatch", "result_binding_mismatch"})
            self.assertNotEqual(old["run_id"], new["run_id"])

    def test_two_dispatches_create_only_one_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init(); outputs = []
            def run() -> None:
                outputs.append(f.command("dispatch", "--campaign", f.campaign, check=False))
            threads = [threading.Thread(target=run) for _ in range(2)]
            [t.start() for t in threads]; [t.join() for t in threads]
            self.assertEqual(1, sum(1 for x in outputs if x["ok"]))
            self.assertEqual(1, sum(1 for x in outputs if x.get("error") == "active_run_exists"))

    def test_target_drift_before_apply_causes_revalidation_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), count=1); f.init()
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(c)))
            _, c = f.dispatch(); audit = f.audit_result(c)
            (f.root / "raw/Q0.md").write_text("external", encoding="utf-8")
            submitted = f.command("submit", "--campaign", f.campaign, "--result", str(audit))
            self.assertEqual({"revalidation_queued": 1}, submitted["counts"])
            self.assertEqual("external", (f.root / "raw/Q0.md").read_text(encoding="utf-8"))

    def test_same_control_plane_supports_import_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = CampaignFixture(Path(tmp), campaign="import-fixture", count=1, profile_id="import")
            f.init(); _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.executor_result(c)))
            _, c = f.dispatch(); f.command("submit", "--campaign", f.campaign, "--result", str(f.audit_result(c)))
            self.assertEqual({"passed": 1}, f.command("status", "--campaign", f.campaign)["counts"])

    def test_two_canary_campaigns_dispatch_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            optics = CampaignFixture(root, campaign="canary-optics", count=1, path_prefix="optics")
            thermal = CampaignFixture(root, campaign="canary-thermal", count=1, path_prefix="thermal")
            optics.init()
            thermal.init()
            _, optics_card = optics.dispatch()
            _, thermal_card = thermal.dispatch()
            self.assertNotEqual(optics_card["run_id"], thermal_card["run_id"])
            self.assertEqual("executor", optics_card["phase"])
            self.assertEqual("executor", thermal_card["phase"])


if __name__ == "__main__":
    unittest.main()
