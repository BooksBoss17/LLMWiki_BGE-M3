from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "learn/exercise-solution-curation/scripts/migrate_p0p1_batches_v3.py"
SPEC = importlib.util.spec_from_file_location("migrate_p0p1_batches_v3", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class P0P1BatchV3MigrationTests(unittest.TestCase):
    counts = {"电磁学": (246, 103), "光学": (101, 25), "力学": (319, 145), "热学": (40, 8), "综合": (127, 114)}

    def test_migrates_833_ordinals_and_keeps_395_source_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kb = Path(directory)
            ledgers = kb / "tmp/tasks/p0p1/ledgers"
            grants = kb / "tmp/tasks/p0p1/mission-v2"
            profile_path = kb / "skills/maintain/agent-campaign-orchestration/profiles/p0p1.json"
            writer = kb / "skills/learn/exercise-solution-curation/scripts/curate_exercise.py"
            writer.parent.mkdir(parents=True)
            writer.write_text("print('fixture')\n", encoding="utf-8")
            profile_path.parent.mkdir(parents=True)
            profile_path.write_text(json.dumps({
                "schema_version": 3,
                "kind": "campaign_profile",
                "profile_id": "p0p1",
                "executor_skills": ["executor"],
                "auditor_skills": ["auditor"],
                "allowed_paths": ["raw/exercises"],
                "model_policy": {
                    "bounded": "test", "ambiguous": "test", "complex": "test", "critical": "test"
                },
                "writer": {"mode": "python", "script": writer.relative_to(kb).as_posix(), "arguments": []},
            }), encoding="utf-8")
            authorization_record = kb / "tmp/tasks/p0p1/user-authorization.json"
            authorization_record.parent.mkdir(parents=True, exist_ok=True)
            authorization_record.write_text("{\"authorized\":true}\n", encoding="utf-8")

            manifest_rows = []
            originals: dict[Path, bytes] = {}
            special = {
                "电磁学": ["MA0000281"],
                "光学": ["C0000888", "C0000891", "C0000913", "MA0000907", "MA0000908"],
                "力学": ["B0000175"],
            }
            for volume, (count, blocked) in self.counts.items():
                campaign = migration.CAMPAIGNS[volume]
                source = kb / f"source-library/{campaign}.docx"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(campaign.encode())
                ids = list(special.get(volume, []))
                ids.extend(f"{campaign.replace('-', '').upper()}{index:04d}" for index in range(count - len(ids)))
                items = []
                for ordinal, qid in enumerate(ids):
                    target = kb / f"raw/exercises/{campaign}/{qid}.md"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(f"---\nid: {qid}\n---\n\n## 题目\n\n题目\n", encoding="utf-8")
                    originals[target] = target.read_bytes()
                    source_ready = qid in special.get(volume, []) or ordinal >= blocked + len(special.get(volume, []))
                    items.append({
                        "question_id": qid,
                        "target_path": target.relative_to(kb).as_posix(),
                        "current_sha256": digest(target),
                        "source_key": source.relative_to(kb).as_posix(),
                        "source_sha256": digest(source),
                        "source_state": "source_ready" if source_ready else "source_blocked",
                        "reported_severities": ["P0" if ordinal == 0 else "P1"],
                        "reasons": [],
                        "report_references": [],
                        "asset_refs": [],
                        "queue_ordinal": ordinal,
                    })
                ledger_path = ledgers / f"{volume}.json"
                ledger_path.parent.mkdir(parents=True, exist_ok=True)
                ledger_path.write_text(json.dumps({
                    "schema_version": 1,
                    "campaign_id": campaign,
                    "volume": volume,
                    "items": items,
                }, ensure_ascii=False), encoding="utf-8")
                manifest_rows.append({"volume": volume, "ledger": str(ledger_path)})
                grant_path = grants / campaign / "authorization-grant.v2.json"
                grant_path.parent.mkdir(parents=True, exist_ok=True)
                grant_path.write_text(json.dumps({
                    "authorized_by": "teacher",
                    "authorization_basis": "fixture authorization",
                    "authorization_record_path": str(authorization_record),
                    "authorization_record_sha256": digest(authorization_record),
                    "allowed_fields": ["stem", "answer", "solution", "assets", "ai_extra_tags", "media"],
                    "writer": "curate_exercise.py",
                    "issued_at": "2026-07-23T00:00:00Z",
                    "expires_at": "2099-07-23T00:00:00Z",
                    "destructive": False,
                }), encoding="utf-8")

            manifest_path = ledgers / "manifest.json"
            manifest_path.write_text(json.dumps({"volumes": manifest_rows}), encoding="utf-8")
            args = SimpleNamespace(
                kb_root=str(kb),
                manifest=str(manifest_path),
                profile=str(profile_path),
                prior_grants_root=str(grants),
                output_root=str(kb / "tmp/tasks/p0p1/batches-v3"),
                retirement_report=str(kb / "skills/_ops/audits/mission-v2-retirement.json"),
            )
            result = migration.migrate(args)
            self.assertTrue(result["ok"])
            self.assertEqual(result["counts"]["total"], 833)
            self.assertEqual(result["counts"]["source_blocked"], 395)
            for campaign in migration.CAMPAIGNS.values():
                state = json.loads((kb / f"skills/_ops/runtime/state/agent_batches_v3/{campaign}/campaign.json").read_text(encoding="utf-8"))
                ordinals = sorted(item["queue_ordinal"] for item in state["items"].values())
                self.assertEqual(ordinals, list(range(len(ordinals))))
            optics = json.loads((kb / "tmp/tasks/p0p1/batches-v3/p0p1-optics/ledger.v3.json").read_text(encoding="utf-8"))
            statuses = {item["question_id"]: item["initial_status"] for item in optics["items"]}
            for qid in special["光学"]:
                self.assertEqual(statuses[qid], "revalidation_queued")
            for path, before in originals.items():
                self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
