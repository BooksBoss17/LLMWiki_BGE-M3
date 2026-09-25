from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch



ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
QUESTION = ROOT / "learn" / "physics-question-tutoring" / "scripts" / "question_pipeline.py"
DIAGRAM = ROOT / "learn" / "physics-diagram-toolkit" / "scripts" / "render_diagram.py"
CURATE = ROOT / "learn" / "exercise-solution-curation" / "scripts" / "curate_exercise.py"
GRAPH = ROOT / "learn" / "physics-knowledge-graph" / "scripts" / "update_graph.py"
TAXONOMY = ROOT / "taxonomy" / "exercise-knowledge-tags" / "scripts" / "manage_taxonomy.py"
RAG_COMPAT = ROOT / "import" / "exercise-bank-import" / "scripts" / "validate_exercise_rag_compat.py"
IMAGE_AUDIT = ROOT / "import" / "exercise-bank-import" / "scripts" / "audit_exercise_images.py"
HANDOFF = ROOT / "_shared" / "scripts" / "import_handoff.py"
RESPONSE_VALIDATOR = ROOT / "learn" / "_shared" / "scripts" / "validate_role_d_response.py"
sys.path.insert(0, str(HANDOFF.parent))
import import_handoff as import_handoff_module  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args: object, expected: int = 0) -> tuple[subprocess.CompletedProcess[str], dict]:
    process = subprocess.run(
        [sys.executable, *(str(item) for item in args)],
        cwd=str(ROOT.parent),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if process.returncode != expected:
        raise AssertionError(f"returncode={process.returncode}\nstdout={process.stdout}\nstderr={process.stderr}")
    stream = process.stdout if process.stdout.strip() else process.stderr
    return process, json.loads(stream)


class RoleDToolTests(unittest.TestCase):
    def test_strong_forward_suite_shape(self) -> None:
        path = ROOT / "learn" / "_shared" / "tests" / "fixtures" / "role_d_strong_eval_cases.json"
        suite = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(suite["model_profile"], "strong")
        self.assertEqual(suite["case_count"], 62)
        counts: dict[str, int] = {}
        for case in suite["cases"]:
            counts[case["category"]] = counts.get(case["category"], 0) + 1
            self.assertFalse(case.get("fixture_spec", {}).get("contains_identity", False))
        self.assertEqual(counts, {"text_physics": 30, "image_vision": 12, "diagram": 8, "governance": 12})

    def test_question_stages_and_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, parsed = run(
                QUESTION,
                "--input",
                FIXTURES / "question_text.txt",
                "--session-id",
                "text-demo",
                "--stage",
                "confirm",
                "--test-mode",
                "--output-root",
                root,
                "--json",
            )
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["parse_status"], "confirmed")
            payload = json.loads(Path(parsed["question_parse"]).read_text(encoding="utf-8"))
            self.assertEqual(len(payload["options"]), 4)
            self.assertNotIn("source_path", payload["source"])

            from PIL import Image

            image_path = root / "blurred.png"
            Image.new("RGB", (320, 200), "white").save(image_path)
            _, abstained = run(
                QUESTION,
                "--input",
                image_path,
                "--session-id",
                "image-demo",
                "--stage",
                "confirm",
                "--test-mode",
                "--output-root",
                root,
                "--json",
            )
            self.assertEqual(abstained["parse_status"], "needs_ocr")
            _, blocked = run(
                QUESTION,
                "--input",
                image_path,
                "--session-id",
                "image-demo",
                "--stage",
                "hint",
                "--test-mode",
                "--output-root",
                root,
                "--json",
                expected=3,
            )
            contract = json.loads(Path(blocked["stage_contract"]).read_text(encoding="utf-8"))
            self.assertFalse(contract["response_constraints"]["reveal_final_answer"])
            self.assertEqual(contract["status"], "blocked")

    def test_strong_profile_vision_review_and_confirmation_binding(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "question.png"
            Image.new("RGB", (640, 360), "white").save(image_path)
            ocr_path = root / "ocr.json"
            ocr_path.write_text(
                json.dumps(
                    {
                        "text": "质量 2 kg 的物体受到 6 N 合力，求加速度。",
                        "confidence": 0.96,
                        "formulas": ["F=ma"],
                        "diagram_objects": [],
                        "uncertain_items": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            _, initial = run(
                QUESTION,
                "--input", image_path,
                "--session-id", "strong-image",
                "--stage", "confirm",
                "--model-profile", "strong",
                "--ocr-json", ocr_path,
                "--test-mode", "--output-root", root / "sessions",
                "--json",
            )
            initial_parse = json.loads(Path(initial["question_parse"]).read_text(encoding="utf-8"))
            self.assertEqual(initial_parse["model_profile"], "strong")
            self.assertIsNone(initial_parse["vision_review"])
            self.assertTrue(any(item["field"] == "vision_review" for item in initial_parse["unconfirmed_items"]))

            review_path = root / "vision-review.json"
            review = {
                "schema_version": 1,
                "source_sha256": digest(image_path),
                "sanitized_sha256": initial_parse["source"]["sanitized_sha256"],
                "review_status": "agree",
                "confidence": 0.97,
                "stem_candidate": "质量 2 kg 的物体受到 6 N 合力，求加速度。",
                "formula_candidates": ["F=ma"],
                "diagram_objects": [],
                "conflicts": [],
                "uncertain_items": [],
            }
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            _, reviewed = run(
                QUESTION,
                "--input", image_path,
                "--session-id", "strong-image",
                "--stage", "confirm",
                "--model-profile", "strong",
                "--ocr-json", ocr_path,
                "--vision-review-json", review_path,
                "--test-mode", "--output-root", root / "sessions",
                "--json",
            )
            reviewed_parse = json.loads(Path(reviewed["question_parse"]).read_text(encoding="utf-8"))
            self.assertEqual(reviewed_parse["status"], "needs_confirmation")
            self.assertEqual(reviewed_parse["vision_review"]["review_status"], "agree")

            conflict_review = dict(review)
            conflict_review["review_status"] = "conflict"
            conflict_review["conflicts"] = [{"field": "stem.mass", "reason": "OCR 为 2 kg，视觉候选为 3 kg"}]
            conflict_path = root / "conflict-review.json"
            conflict_path.write_text(json.dumps(conflict_review, ensure_ascii=False), encoding="utf-8")
            _, conflict = run(
                QUESTION,
                "--input", image_path,
                "--session-id", "conflict-image",
                "--stage", "solution",
                "--model-profile", "strong",
                "--ocr-json", ocr_path,
                "--vision-review-json", conflict_path,
                "--test-mode", "--output-root", root / "sessions",
                "--json",
                expected=3,
            )
            self.assertEqual(conflict["stage_status"], "blocked")

            _, confirmed = run(
                QUESTION,
                "--input", image_path,
                "--session-id", "strong-image",
                "--stage", "solution",
                "--model-profile", "strong",
                "--ocr-json", ocr_path,
                "--vision-review-json", review_path,
                "--confirmed",
                "--test-mode", "--output-root", root / "sessions",
                "--json",
            )
            self.assertEqual(confirmed["stage_status"], "ready_for_model")

            ocr_path.write_text(
                json.dumps({"text": "质量 3 kg 的物体受到 6 N 合力。", "confidence": 0.96}, ensure_ascii=False),
                encoding="utf-8",
            )
            _, stale = run(
                QUESTION,
                "--input", image_path,
                "--session-id", "strong-image",
                "--stage", "solution",
                "--model-profile", "strong",
                "--ocr-json", ocr_path,
                "--vision-review-json", review_path,
                "--test-mode", "--output-root", root / "sessions",
                "--json",
                expected=3,
            )
            self.assertEqual(stale["stage_status"], "blocked")

            bad_review = dict(review)
            bad_review["sanitized_sha256"] = "0" * 64
            bad_path = root / "bad-review.json"
            bad_path.write_text(json.dumps(bad_review, ensure_ascii=False), encoding="utf-8")
            run(
                QUESTION,
                "--input", image_path,
                "--session-id", "bad-review",
                "--stage", "confirm",
                "--model-profile", "strong",
                "--ocr-json", ocr_path,
                "--vision-review-json", bad_path,
                "--test-mode", "--output-root", root / "sessions",
                "--json",
                expected=2,
            )

            _, no_ocr = run(
                QUESTION,
                "--input", image_path,
                "--session-id", "vision-candidate",
                "--stage", "confirm",
                "--model-profile", "strong",
                "--test-mode", "--output-root", root / "sessions",
                "--json",
            )
            no_ocr_parse = json.loads(Path(no_ocr["question_parse"]).read_text(encoding="utf-8"))
            candidate_review = dict(review)
            candidate_review["sanitized_sha256"] = no_ocr_parse["source"]["sanitized_sha256"]
            candidate_review["review_status"] = "partial"
            candidate_path = root / "candidate-review.json"
            candidate_path.write_text(json.dumps(candidate_review, ensure_ascii=False), encoding="utf-8")
            _, candidate = run(
                QUESTION,
                "--input", image_path,
                "--session-id", "vision-candidate",
                "--stage", "confirm",
                "--model-profile", "strong",
                "--vision-review-json", candidate_path,
                "--test-mode", "--output-root", root / "sessions",
                "--json",
            )
            candidate_parse = json.loads(Path(candidate["question_parse"]).read_text(encoding="utf-8"))
            self.assertEqual(candidate_parse["status"], "needs_confirmation")
            self.assertTrue(candidate_parse["stem"])

    def test_role_d_response_validation_and_numeric_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            question = root / "question.txt"
            question.write_text("质量 2 kg 的物体受到 6 N 合力，求加速度。", encoding="utf-8")
            _, parsed = run(
                QUESTION,
                "--input", question,
                "--session-id", "response-validation",
                "--stage", "solution",
                "--model-profile", "strong",
                "--test-mode", "--output-root", root / "sessions",
                "--json",
            )
            contract = Path(parsed["stage_contract"])
            response = {
                "schema_version": 1,
                "stage": "solution",
                "action": "solution",
                "answer_kind": "numeric",
                "sections": {
                    "given": ["m=2 kg", "F=6 N"],
                    "model": "质点模型",
                    "derivation": "由 F=ma 得 a=F/m",
                    "answer": "3 m/s^2",
                    "verification": "量纲正确",
                    "common_errors": ["把合力当作某一个力"],
                },
                "calculation_check": {"expression": "6/2", "claimed_value": 3, "unit": "m/s^2", "retry_count": 0},
            }
            response_path = root / "response.json"
            response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            _, accepted = run(RESPONSE_VALIDATOR, "--contract", contract, "--response", response_path, "--json")
            self.assertEqual(accepted["status"], "accepted")

            response["calculation_check"]["claimed_value"] = 4
            response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            _, retry = run(RESPONSE_VALIDATOR, "--contract", contract, "--response", response_path, "--json", expected=3)
            self.assertEqual(retry["status"], "retry_required")
            response["calculation_check"]["retry_count"] = 1
            response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            _, stopped = run(RESPONSE_VALIDATOR, "--contract", contract, "--response", response_path, "--json", expected=3)
            self.assertEqual(stopped["status"], "needs_confirmation")

            response["calculation_check"] = {
                "expression": "__import__('os').system('echo unsafe')",
                "claimed_value": 0,
                "unit": "m/s^2",
                "retry_count": 1,
            }
            response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            _, unsafe = run(RESPONSE_VALIDATOR, "--contract", contract, "--response", response_path, "--json", expected=3)
            self.assertTrue(any("unsupported calculation node" in issue for issue in unsafe["issues"]))

            _, hint_stage = run(
                QUESTION,
                "--input", question,
                "--session-id", "hint-leak",
                "--stage", "hint",
                "--model-profile", "strong",
                "--test-mode", "--output-root", root / "sessions",
                "--json",
            )
            hint_response = {
                "schema_version": 1,
                "stage": "hint",
                "action": "hint",
                "answer_kind": "none",
                "sections": {"single_hint": "使用 F=ma", "check_question": "合力是多少？", "answer": "3 m/s^2"},
            }
            hint_path = root / "hint.json"
            hint_path.write_text(json.dumps(hint_response, ensure_ascii=False), encoding="utf-8")
            _, leaked = run(RESPONSE_VALIDATOR, "--contract", hint_stage["stage_contract"], "--response", hint_path, "--json", expected=3)
            self.assertIn("answer", json.dumps(leaked["issues"]))

    def test_diagram_render_and_source_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, report = run(DIAGRAM, "--spec", FIXTURES / "diagram_free_body.json", "--out-dir", root / "free-body", "--test-mode", "--json")
            self.assertTrue(report["ok"])
            self.assertTrue(Path(report["png"]).stat().st_size > 100)
            self.assertGreaterEqual(report["validation"]["arrow_count"], 2)

            from PIL import Image, ImageDraw

            source = root / "source.png"
            source_image = Image.new("RGBA", (400, 300), (255, 255, 255, 0))
            ImageDraw.Draw(source_image).rectangle((180, 120, 220, 180), fill=(0, 0, 0, 255))
            source_image.save(source)
            before = digest(source)
            spec = {
                "schema_version": 1,
                "kind": "annotation",
                "canvas": {"width": 400, "height": 300, "background": "#ffffff"},
                "background_image": str(source),
                "primitives": [{"type": "arrow", "x1": 80, "y1": 220, "x2": 300, "y2": 80, "label": "速度 v", "color": "#d12b2b"}],
            }
            spec_path = root / "annotation.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            _, annotated = run(DIAGRAM, "--spec", spec_path, "--out-dir", root / "annotation", "--test-mode", "--json")
            self.assertTrue(annotated["source_unchanged"])
            self.assertEqual(before, digest(source))
            with Image.open(annotated["sanitized_background"]) as sanitized:
                rgb = sanitized.convert("RGB")
                self.assertEqual(rgb.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(rgb.getpixel((200, 150)), (0, 0, 0))

            bad_revision = json.loads((FIXTURES / "diagram_free_body.json").read_text(encoding="utf-8"))
            bad_revision["revision"] = 3
            bad_revision_path = root / "bad-revision.json"
            bad_revision_path.write_text(json.dumps(bad_revision, ensure_ascii=False), encoding="utf-8")
            run(DIAGRAM, "--spec", bad_revision_path, "--out-dir", root / "bad-revision", "--test-mode", "--json", expected=2)

    def test_diagram_strong_curator_writes_rendered_image_to_question_bank(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            rendered_dir = kb / "output" / "learning_sessions" / "diagram-curation" / "assets"
            _, rendered = run(
                DIAGRAM,
                "--spec", FIXTURES / "diagram_free_body.json",
                "--out-dir", rendered_dir,
                "--test-mode", "--json",
            )
            self.assertTrue(rendered["ok"])
            rendered_png = Path(rendered["png"])

            target = kb / "raw" / "exercises" / "demo" / "MC0000003.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "---\nid: MC0000003\nknowledge_points:\n- 力学/相互作用/共点力的平衡\nassets: []\n---\n\n"
                "# MC0000003\n\n## 题目\n\n物体处于平衡状态。\n\n## 答案\n\nA\n\n## 详解\n\n根据平衡条件进行分析。\n",
                encoding="utf-8",
            )
            proposal = {
                "schema_version": 2,
                "model_profile": "strong",
                "question_id": "MC0000003",
                "target_path": "raw/exercises/demo/MC0000003.md",
                "expected_sha256": digest(target),
                "updates": {
                    "solution": "根据平衡条件作受力图并逐方向列式。\n\n![受力图](media/MC0000003_solution01.png)\n\n图中各力方向与题意一致。",
                    "assets": ["media/MC0000003_solution01.png"],
                    "ai_extra_tags": ["受力分析", "图解法"],
                },
                "media_files": [{
                    "source_path": f"output/learning_sessions/diagram-curation/assets/{rendered_png.name}",
                    "source_sha256": digest(rendered_png),
                    "target": "media/MC0000003_solution01.png",
                }],
                "verification": {
                    "source_evidence": [{
                        "path": f"output/learning_sessions/diagram-curation/assets/{rendered_png.name}",
                        "sha256": digest(rendered_png),
                    }],
                    "stem_complete": True,
                    "figure_dependencies_resolved": True,
                    "review_passes": 2,
                    "derived_correct_options": ["A"],
                },
            }
            proposal_path = kb / "diagram-curation-v2.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            _, dry = run(CURATE, "--proposal", proposal_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json")
            self.assertFalse(dry["applied"])
            _, applied = run(
                CURATE,
                "--proposal", proposal_path,
                "--apply", "--teacher-authorized",
                "--test-mode", "--kb-root", kb,
                "--json",
            )
            self.assertTrue(applied["applied"])
            updated = target.read_text(encoding="utf-8")
            self.assertIn("![受力图](media/MC0000003_solution01.png)", updated)
            self.assertIn("ai_extra_tags:\n- 受力分析\n- 图解法", updated)
            self.assertEqual(
                digest(target.parent / "media" / "MC0000003_solution01.png"),
                digest(rendered_png),
            )

    def test_curation_authorization_and_hash_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            target = kb / "raw" / "exercises" / "demo" / "MC0000001.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "---\nid: MC0000001\nknowledge_points:\n  - 力学/牛顿运动定律/牛顿第二定律\n---\n\n## 题目\n\n示例题。\n\n## 答案\n\nA\n\n## 详解\n\n原解析内容足够长但需要修正。\n",
                encoding="utf-8",
            )
            proposal = {
                "schema_version": 1,
                "question_id": "MC0000001",
                "target_path": "raw/exercises/demo/MC0000001.md",
                "expected_sha256": digest(target),
                "updates": {"answer": "B", "solution": "由牛顿第二定律列式 $F=ma$，代入已知量并核对单位，得到选项 B。"},
            }
            proposal_path = kb / "proposal.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            _, dry = run(CURATE, "--proposal", proposal_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json")
            self.assertFalse(dry["applied"])
            run(CURATE, "--proposal", proposal_path, "--apply", "--test-mode", "--kb-root", kb, "--json", expected=2)
            _, applied = run(CURATE, "--proposal", proposal_path, "--apply", "--teacher-authorized", "--test-mode", "--kb-root", kb, "--json")
            self.assertTrue(applied["applied"])
            self.assertIn("## 答案\n\nB", target.read_text(encoding="utf-8"))
            run(CURATE, "--proposal", proposal_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json", expected=2)

    def test_v3_batch_curation_requires_independent_audit_and_emits_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            target = kb / "raw/exercises/demo/MC0000099.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "---\nid: MC0000099\nknowledge_points:\n  - 力学/牛顿运动定律\n---\n\n"
                "## 题目\n\n示例题。\n\n## 答案\n\nA\n\n## 详解\n\n原解析。\n",
                encoding="utf-8",
            )
            proposal = {
                "schema_version": 1,
                "question_id": "MC0000099",
                "target_path": "raw/exercises/demo/MC0000099.md",
                "expected_sha256": digest(target),
                "updates": {"answer": "B", "solution": "独立推导并核对题干条件后，正确选项为 B。"},
            }
            campaign_root = kb / "skills/_ops/runtime/state/agent_batches_v3/writer-test"
            artifact_root = campaign_root / "artifacts/batch-writer-test"
            proposal_path = artifact_root / "proposal.json"
            proposal_path.parent.mkdir(parents=True)
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            ledger_path = campaign_root / "ledger.json"
            ledger_path.write_text(json.dumps({"items": [{"question_id": "MC0000099"}]}), encoding="utf-8")
            authorization_record = campaign_root / "user-authorization.json"
            authorization_record.write_text(json.dumps({"authorized": True}), encoding="utf-8")
            grant = {
                "schema_version": 3,
                "grant_id": "grant-writer-test",
                "campaign_id": "writer-test",
                "ledger_sha256": digest(ledger_path),
                "authorized_by": "teacher",
                "authorization_record_path": str(authorization_record.resolve()),
                "authorization_record_sha256": digest(authorization_record),
                "allowed_fields": ["answer", "solution"],
                "writer": "exercise-solution-curation/curate_exercise.py",
                "expires_at": "2099-01-01T00:00:00Z",
                "destructive": False,
            }
            grant_path = campaign_root / "authorization.json"
            grant_path.parent.mkdir(parents=True, exist_ok=True)
            grant_path.write_text(json.dumps(grant), encoding="utf-8")
            executor_card = {
                "schema_version": 3,
                "kind": "batch_card",
                "campaign_id": "writer-test",
                "batch_id": "batch-writer-test",
                "run_id": "executor-run",
                "phase": "executor",
                "revision_no": 0,
                "required_capability": "complex",
                "items": [
                    {
                        "question_id": "MC0000099",
                        "target_ref": {
                            "relative_path": "raw/exercises/demo/MC0000099.md",
                            "sha256": digest(target),
                        },
                    }
                ],
                "artifact_root": str(artifact_root),
                "result_path": str(artifact_root / "executor-result.json"),
            }
            executor_card_path = artifact_root / "executor-card.json"
            executor_card_path.write_text(json.dumps(executor_card), encoding="utf-8")
            campaign_state = {
                "schema_version": 3,
                "campaign_id": "writer-test",
                "ledger_ref": {"path": str(ledger_path.resolve()), "sha256": digest(ledger_path)},
                "authorization_ref": {"path": str(grant_path.resolve()), "sha256": digest(grant_path)},
                "active_run": {
                    "batch_id": "batch-writer-test",
                    "run_id": "executor-run",
                    "phase": "executor",
                    "card_ref": {"path": str(executor_card_path), "sha256": digest(executor_card_path)},
                },
            }
            state_path = campaign_root / "campaign.json"
            state_path.write_text(json.dumps(campaign_state), encoding="utf-8")
            dry_receipt = artifact_root / "MC0000099.dry-run.json"
            _, dry = run(
                CURATE,
                "--proposal", proposal_path,
                "--dry-run",
                "--batch-card", executor_card_path,
                "--batch-card-sha256", digest(executor_card_path),
                "--authorization", grant_path,
                "--authorization-sha256", digest(grant_path),
                "--receipt-out", dry_receipt,
                "--test-mode", "--kb-root", kb,
                "--json",
            )
            self.assertEqual(dry["dry_run_receipt"]["sha256"], digest(dry_receipt))
            dry_payload = json.loads(dry_receipt.read_text(encoding="utf-8"))
            self.assertEqual(dry_payload["status"], "verified")

            auditor_card = {**executor_card, "run_id": "auditor-run", "phase": "auditor"}
            auditor_card_path = artifact_root / "auditor-card.json"
            auditor_card_path.write_text(json.dumps(auditor_card), encoding="utf-8")
            audit_result = {
                "schema_version": 3,
                "kind": "batch_result",
                "campaign_id": "writer-test",
                "batch_id": "batch-writer-test",
                "run_id": "auditor-run",
                "phase": "auditor",
                "batch_card_sha256": digest(auditor_card_path),
                "items": [{"question_id": "MC0000099", "verdict": "passed"}],
            }
            audit_result_path = artifact_root / "audit-result.json"
            audit_result_path.write_text(json.dumps(audit_result), encoding="utf-8")
            campaign_state["active_run"] = {
                "batch_id": "batch-writer-test",
                "run_id": "auditor-run",
                "phase": "applying",
                "card_ref": {"path": str(auditor_card_path), "sha256": digest(auditor_card_path)},
            }
            state_path.write_text(json.dumps(campaign_state), encoding="utf-8")
            apply_receipt = campaign_root / "receipts/batch-writer-test/MC0000099.apply.json"
            apply_receipt.parent.mkdir(parents=True)
            _, applied = run(
                CURATE,
                "--proposal", proposal_path,
                "--apply", "--teacher-authorized",
                "--batch-card", auditor_card_path,
                "--batch-card-sha256", digest(auditor_card_path),
                "--audit-result", audit_result_path,
                "--audit-result-sha256", digest(audit_result_path),
                "--authorization", grant_path,
                "--authorization-sha256", digest(grant_path),
                "--dry-run-receipt", dry_receipt,
                "--dry-run-receipt-sha256", digest(dry_receipt),
                "--receipt-out", apply_receipt,
                "--test-mode", "--kb-root", kb,
                "--json",
            )
            self.assertTrue(applied["applied"])
            self.assertEqual(applied["apply_receipt"]["sha256"], digest(apply_receipt))
            apply_payload = json.loads(apply_receipt.read_text(encoding="utf-8"))
            receipt_schema = json.loads(
                (ROOT / "_shared/schemas/apply_receipt_v3.schema.json").read_text(encoding="utf-8")
            )
            from jsonschema import Draft202012Validator

            Draft202012Validator(receipt_schema).validate(apply_payload)
            self.assertEqual(apply_payload["status"], "applied")
            self.assertEqual(apply_payload["batch_card_sha256"], digest(auditor_card_path))
            self.assertEqual(apply_payload["proposal"]["sha256"], digest(proposal_path))
            self.assertEqual(apply_payload["dry_run"]["sha256"], digest(dry_receipt))
            self.assertEqual(apply_payload["files"][0]["after_sha256"], digest(target))
            self.assertEqual(apply_payload["files"][0]["backup_sha256"], proposal["expected_sha256"])
            journal_path = Path(applied["transaction_journal"]["path"])
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertEqual(journal["status"], "committed")

    def test_strong_curation_updates_full_question_and_adds_hashed_media(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            target = kb / "raw" / "exercises" / "demo" / "MC0000002.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "---\nid: MC0000002\nknowledge_points:\n  - 力学/相互作用/摩擦力\nassets: []\n---\n\n"
                "# MC0000002\n\n## 题目\n\n原题干。\n\n## 答案\n\nA\n\n## 详解\n\n原解析内容足够长但需要修正。\n",
                encoding="utf-8",
            )
            staged = kb / "staging" / "diagram.png"
            staged.parent.mkdir(parents=True)
            Image.new("RGB", (160, 100), "white").save(staged)
            proposal = {
                "schema_version": 2,
                "model_profile": "strong",
                "question_id": "MC0000002",
                "target_path": "raw/exercises/demo/MC0000002.md",
                "expected_sha256": digest(target),
                "updates": {
                    "stem": "修正后的完整题干。\n\n![受力图](media/MC0000002_solution01.png)\n\nA. 甲  B. 乙",
                    "answer": "B",
                    "solution": r"由平衡条件和 $f=\mu N$ 逐项判断，受力图与方程一致，因此选择 B。",
                    "assets": ["media/MC0000002_solution01.png"],
                    "ai_extra_tags": ["受力分析", "易错-方向判断"],
                },
                "media_files": [{
                    "source_path": "staging/diagram.png",
                    "source_sha256": digest(staged),
                    "target": "media/MC0000002_solution01.png",
                }],
                "verification": {
                    "source_evidence": [{"path": "staging/diagram.png", "sha256": digest(staged)}],
                    "stem_complete": True,
                    "figure_dependencies_resolved": True,
                    "review_passes": 2,
                    "derived_correct_options": ["B"],
                },
            }
            proposal_path = kb / "proposal-v2.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")

            _, dry = run(CURATE, "--proposal", proposal_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json")
            self.assertEqual(dry["model_profile"], "strong")
            self.assertEqual(dry["media_files"][0]["action"], "create")
            self.assertFalse((target.parent / "media" / "MC0000002_solution01.png").exists())

            run(CURATE, "--proposal", proposal_path, "--apply", "--test-mode", "--kb-root", kb, "--json", expected=2)
            _, applied = run(
                CURATE,
                "--proposal", proposal_path,
                "--apply", "--teacher-authorized",
                "--test-mode", "--kb-root", kb,
                "--json",
            )
            self.assertTrue(applied["applied"])
            updated = target.read_text(encoding="utf-8")
            self.assertIn("## 题目\n\n修正后的完整题干", updated)
            self.assertIn("assets:\n- media/MC0000002_solution01.png", updated)
            self.assertIn("ai_extra_tags:\n- 受力分析\n- 易错-方向判断", updated)
            self.assertIn(r"$f=\mu N$", updated)
            self.assertTrue((target.parent / "media" / "MC0000002_solution01.png").is_file())

            weak_proposal = dict(proposal)
            weak_proposal["model_profile"] = "weak"
            weak_proposal["expected_sha256"] = digest(target)
            weak_path = kb / "weak-v2.json"
            weak_path.write_text(json.dumps(weak_proposal, ensure_ascii=False), encoding="utf-8")
            run(CURATE, "--proposal", weak_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json", expected=2)

            invalid_ai_tags = dict(proposal)
            invalid_ai_tags["updates"] = dict(proposal["updates"])
            invalid_ai_tags["updates"]["ai_extra_tags"] = ["kp_000032"]
            invalid_ai_tags["expected_sha256"] = digest(target)
            invalid_ai_tags_path = kb / "invalid-ai-tags-v2.json"
            invalid_ai_tags_path.write_text(json.dumps(invalid_ai_tags, ensure_ascii=False), encoding="utf-8")
            run(CURATE, "--proposal", invalid_ai_tags_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json", expected=2)

            replacement = kb / "staging" / "different.png"
            Image.new("RGB", (160, 100), "black").save(replacement)
            overwrite = dict(proposal)
            overwrite["expected_sha256"] = digest(target)
            overwrite["media_files"] = [{
                "source_path": "staging/different.png",
                "source_sha256": digest(replacement),
                "target": "media/MC0000002_solution01.png",
                "expected_target_sha256": digest(target.parent / "media" / "MC0000002_solution01.png"),
            }]
            overwrite_path = kb / "overwrite-v2.json"
            overwrite_path.write_text(json.dumps(overwrite, ensure_ascii=False), encoding="utf-8")
            _, replace_dry = run(CURATE, "--proposal", overwrite_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json")
            self.assertEqual(replace_dry["media_files"][0]["action"], "replace")
            _, replaced = run(
                CURATE,
                "--proposal", overwrite_path,
                "--apply", "--teacher-authorized",
                "--test-mode", "--kb-root", kb,
                "--json",
            )
            media_target = target.parent / "media" / "MC0000002_solution01.png"
            self.assertEqual(digest(media_target), digest(replacement))
            self.assertEqual(len(replaced["media_backups"]), 1)
            self.assertNotEqual(digest(Path(replaced["media_backups"][0])), digest(media_target))

            stale_overwrite = dict(overwrite)
            stale_overwrite["expected_sha256"] = digest(target)
            stale_overwrite["media_files"] = [dict(overwrite["media_files"][0])]
            stale_overwrite["media_files"][0]["expected_target_sha256"] = "0" * 64
            stale_path = kb / "stale-overwrite-v2.json"
            stale_path.write_text(json.dumps(stale_overwrite, ensure_ascii=False), encoding="utf-8")
            run(CURATE, "--proposal", stale_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json", expected=2)

            missing_verification = dict(proposal)
            missing_verification.pop("verification")
            missing_verification["expected_sha256"] = digest(target)
            missing_path = kb / "missing-verification-v2.json"
            missing_path.write_text(json.dumps(missing_verification, ensure_ascii=False), encoding="utf-8")
            run(CURATE, "--proposal", missing_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json", expected=2)

            mismatched_options = dict(proposal)
            mismatched_options["expected_sha256"] = digest(target)
            mismatched_options["verification"] = dict(proposal["verification"])
            mismatched_options["verification"]["derived_correct_options"] = ["A"]
            mismatch_path = kb / "mismatched-options-v2.json"
            mismatch_path.write_text(json.dumps(mismatched_options, ensure_ascii=False), encoding="utf-8")
            run(CURATE, "--proposal", mismatch_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json", expected=2)

    def test_missing_explicit_figure_is_a_hard_issue_and_blocks_curation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            exercise_root = kb / "raw" / "exercises" / "demo"
            exercise_root.mkdir(parents=True)
            missing = exercise_root / "MC0000004.md"
            missing.write_text(
                "---\nid: MC0000004\nknowledge_points:\n- 力学/运动的描述/机械运动\nassets: []\n---\n\n"
                "# MC0000004\n\n## 题目\n\n如图所示，物体沿斜面运动。\n\n## 答案\n\nA\n\n## 详解\n\n原解析内容足够长但需要修正。\n",
                encoding="utf-8",
            )
            generic = exercise_root / "MC0000005.md"
            generic.write_text(
                "---\nid: MC0000005\nknowledge_points:\n- 力学/运动的描述/机械运动\nassets: []\n---\n\n"
                "# MC0000005\n\n## 题目\n\n本题考查图像法和图像特征。\n\n## 答案\n\nA\n\n## 详解\n\n根据函数关系判断即可。\n",
                encoding="utf-8",
            )
            plain = exercise_root / "MC0000006.md"
            plain.write_text(
                "---\nid: MC0000006\nknowledge_points:\n- 力学/运动的描述/机械运动\nassets: []\n---\n\n"
                "# MC0000006\n\n## 题目\n\n物体做匀速直线运动。\n\n## 答案\n\nA\n\n## 详解\n\n速度保持不变。\n",
                encoding="utf-8",
            )
            _, audit = run(
                IMAGE_AUDIT, exercise_root,
                "--kb-root", kb,
                "--out", kb / "audit",
                "--no-fail", "--json",
            )
            self.assertEqual(audit["issue_count"], 1)
            self.assertEqual(audit["semantic_missing_figure_count"], 1)
            issues = json.loads((kb / "audit" / "issues.json").read_text(encoding="utf-8"))
            self.assertEqual(issues[0]["code"], "stem_requires_figure_but_assets_empty")

            proposal = {
                "schema_version": 1,
                "question_id": "MC0000004",
                "target_path": "raw/exercises/demo/MC0000004.md",
                "expected_sha256": digest(missing),
                "updates": {
                    "answer": "A",
                    "solution": "由斜面方向的运动关系进行分析，逐项核对后可知正确答案为 A。",
                },
            }
            proposal_path = kb / "missing-figure-proposal.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            run(CURATE, "--proposal", proposal_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json", expected=2)

    def test_non_choice_strong_verification_requires_confirmed_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            target = kb / "raw" / "exercises" / "demo" / "B0000001.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "---\nid: B0000001\nknowledge_points:\n- 力学/运动的描述/机械运动\nassets: []\n---\n\n"
                "# B0000001\n\n## 题目\n\n求物体的速度。\n\n## 答案\n\n2 m/s\n\n## 详解\n\n原解析内容足够长但需要修正。\n",
                encoding="utf-8",
            )
            proposal = {
                "schema_version": 2,
                "model_profile": "strong",
                "question_id": "B0000001",
                "target_path": "raw/exercises/demo/B0000001.md",
                "expected_sha256": digest(target),
                "updates": {"solution": "由位移与时间的比值计算速度，代入数据得到 $v=2\\,m/s$。"},
                "verification": {
                    "source_evidence": [{
                        "path": "raw/exercises/demo/B0000001.md",
                        "sha256": digest(target),
                    }],
                    "stem_complete": True,
                    "figure_dependencies_resolved": True,
                    "review_passes": 2,
                    "non_choice_check": {
                        "status": "confirmed",
                        "summary": "独立使用速度定义式复算，量纲和数值均与最终答案一致。",
                    },
                },
            }
            proposal_path = kb / "non-choice-v2.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            _, dry = run(CURATE, "--proposal", proposal_path, "--dry-run", "--test-mode", "--kb-root", kb, "--json")
            self.assertEqual(dry["verification"]["non_choice_check"]["status"], "confirmed")

    def test_taxonomy_stable_id_and_destructive_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            registry = kb / "knowledge-points.yaml"
            skill_doc = kb / "SKILL.md"
            shutil.copy2(ROOT / "taxonomy" / "exercise-knowledge-tags" / "references" / "knowledge-points.yaml", registry)
            shutil.copy2(ROOT / "taxonomy" / "exercise-knowledge-tags" / "SKILL.md", skill_doc)
            proposal = {
                "schema_version": 1,
                "expected_registry_sha256": digest(registry),
                "changes": [{"action": "add", "title": "合成测试标签", "parent_id": None}],
            }
            proposal_path = kb / "taxonomy.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            _, dry = run(TAXONOMY, "--proposal", proposal_path, "--dry-run", "--test-mode", "--kb-root", kb, "--registry", registry, "--skill-doc", skill_doc, "--json")
            self.assertEqual(dry["allocated_ids"], ["kp_000214"])
            run(TAXONOMY, "--proposal", proposal_path, "--apply", "--test-mode", "--kb-root", kb, "--registry", registry, "--skill-doc", skill_doc, "--json", expected=2)
            _, applied = run(TAXONOMY, "--proposal", proposal_path, "--apply", "--teacher-authorized", "--test-mode", "--kb-root", kb, "--registry", registry, "--skill-doc", skill_doc, "--json")
            self.assertTrue(applied["applied"])
            self.assertIn("kp_000214", registry.read_text(encoding="utf-8"))
            destructive = {
                "schema_version": 1,
                "expected_registry_sha256": digest(registry),
                "changes": [{"action": "rename", "kp_id": "kp_000214", "new_title": "改名测试标签"}],
            }
            destructive_path = kb / "destructive.json"
            destructive_path.write_text(json.dumps(destructive, ensure_ascii=False), encoding="utf-8")
            run(TAXONOMY, "--proposal", destructive_path, "--apply", "--teacher-authorized", "--test-mode", "--kb-root", kb, "--registry", registry, "--skill-doc", skill_doc, "--json", expected=2)

    def test_knowledge_points_compatibility_and_taxonomy_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            target = kb / "raw" / "exercises" / "demo" / "MC0000001.md"
            target.parent.mkdir(parents=True)
            target.write_text(
                "---\nid: MC0000001\nquestion_type: MC\nknowledge_points:\n- 力学/运动的描述/机械运动\n---\n\n## 题目\n\n示例题。\n\n## 答案\n\nA\n\n## 详解\n\n示例详解。\n",
                encoding="utf-8",
            )
            _, validation = run(RAG_COMPAT, target, "--kb-root", kb, "--json")
            self.assertTrue(validation["ok"])
            self.assertEqual(validation["results"][0]["knowledge_points"], ["力学/运动的描述/机械运动"])

            registry = kb / "knowledge-points.yaml"
            skill_doc = kb / "SKILL.md"
            shutil.copy2(ROOT / "taxonomy" / "exercise-knowledge-tags" / "references" / "knowledge-points.yaml", registry)
            shutil.copy2(ROOT / "taxonomy" / "exercise-knowledge-tags" / "SKILL.md", skill_doc)
            proposal = {
                "schema_version": 1,
                "expected_registry_sha256": digest(registry),
                "changes": [{
                    "action": "assign_question",
                    "target_path": "raw/exercises/demo/MC0000001.md",
                    "expected_sha256": digest(target),
                    "knowledge_point_ids": ["kp_000003"],
                    "ai_extra_tags": ["基础概念", "易错-参考系"],
                }],
            }
            proposal_path = kb / "taxonomy-assign.json"
            proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
            _, applied = run(TAXONOMY, "--proposal", proposal_path, "--apply", "--teacher-authorized", "--test-mode", "--kb-root", kb, "--registry", registry, "--skill-doc", skill_doc, "--json")
            self.assertTrue(applied["applied"])
            updated = target.read_text(encoding="utf-8")
            self.assertEqual(updated.count("knowledge_points:"), 1)
            self.assertIn("knowledge_points:\n- 力学/运动的描述/机械运动\nknowledge_point_ids:\n  - kp_000003", updated)
            self.assertIn("ai_extra_tags:\n  - 基础概念\n  - 易错-参考系", updated)

    def test_graph_safe_apply_and_import_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            concepts = kb / "LLMWiki" / "concepts"
            raw = kb / "raw" / "textbooks"
            concepts.mkdir(parents=True)
            raw.mkdir(parents=True)
            root_page = concepts / "高中物理知识关系图谱.md"
            target_page = concepts / "牛顿运动定律.md"
            unrelated = concepts / "无关页.md"
            root_page.write_text("# 高中物理知识关系图谱\n", encoding="utf-8")
            target_page.write_text("# 牛顿运动定律\n", encoding="utf-8")
            unrelated.write_text("# 无关页\n", encoding="utf-8")
            raw_file = raw / "demo.md"
            raw_file.write_text("# 合成教材\n", encoding="utf-8")
            config = kb / "managed.yaml"
            config.write_text(
                "version: 1\nroot: 高中物理知识关系图谱.md\nmanaged_pages:\n  - 高中物理知识关系图谱.md\n  - 牛顿运动定律.md\n",
                encoding="utf-8",
            )
            spec = {
                "handoff_id": "synthetic-import-001",
                "source_skill": "textbook-import",
                "changed_raw_files": ["raw/textbooks/demo.md"],
                "changed_wiki_files": [],
                "question_ids": [],
                "kp_ids": ["kp_000032"],
                "quality_gates": {"passed": True, "unresolved_issues": 0, "reports": []},
                "graph_changes": [{"action": "append_relation", "page": "高中物理知识关系图谱.md", "text": "- [[牛顿运动定律]]：合成导入关系。"}],
            }
            spec_path = kb / "handoff-spec.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            handoff_path = kb / "runtime" / "import_handoff.json"
            _, created = run(HANDOFF, "create", "--spec", spec_path, "--out", handoff_path, "--test-mode", "--kb-root", kb, "--json")
            self.assertTrue(created["requires_graph"])
            result_path = kb / "runtime" / "graph_update_result.json"
            _, dry = run(GRAPH, "--handoff", handoff_path, "--dry-run", "--test-mode", "--kb-root", kb, "--managed-config", config, "--json")
            self.assertEqual(dry["status"], "dry_run")
            unrelated_hash = digest(unrelated)
            _, applied = run(GRAPH, "--handoff", handoff_path, "--apply", "--result-out", result_path, "--test-mode", "--kb-root", kb, "--managed-config", config, "--json")
            self.assertEqual(applied["status"], "applied_safe")
            self.assertIn("ROLE_D_MANAGED", root_page.read_text(encoding="utf-8"))
            self.assertEqual(unrelated_hash, digest(unrelated))
            with patch.object(import_handoff_module, "DEFAULT_KB_ROOT", kb):
                verify_exit = import_handoff_module.main(
                    [
                        "verify",
                        "--handoff",
                        str(handoff_path),
                        "--graph-result",
                        str(result_path),
                        "--json",
                    ]
                )
            self.assertEqual(verify_exit, 0)
            verified = json.loads((handoff_path.parent / "handoff_completion.json").read_text(encoding="utf-8"))
            self.assertTrue(verified["rag_rebuild_allowed"])

            destructive_spec = dict(spec)
            destructive_spec["handoff_id"] = "synthetic-import-002"
            destructive_spec["graph_changes"] = [{"action": "delete_page", "page": "牛顿运动定律.md"}]
            destructive_spec_path = kb / "destructive-handoff-spec.json"
            destructive_spec_path.write_text(json.dumps(destructive_spec, ensure_ascii=False), encoding="utf-8")
            destructive_handoff = kb / "runtime" / "destructive_handoff.json"
            run(HANDOFF, "create", "--spec", destructive_spec_path, "--out", destructive_handoff, "--test-mode", "--kb-root", kb, "--json")
            _, pending = run(GRAPH, "--handoff", destructive_handoff, "--apply", "--test-mode", "--kb-root", kb, "--managed-config", config, "--json")
            self.assertEqual(pending["status"], "approval_required")
            self.assertTrue(target_page.exists())


if __name__ == "__main__":
    unittest.main()
