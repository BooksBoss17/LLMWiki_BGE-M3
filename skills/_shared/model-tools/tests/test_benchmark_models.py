from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import benchmark_models  # noqa: E402


def test_validation_lock_uses_measured_texteller_device_and_cudnn_status(monkeypatch, tmp_path):
    monkeypatch.setattr(benchmark_models, "runtime_root", lambda: tmp_path)
    payload = {
        "generated_at": "2026-07-17T00:00:00+00:00",
        "suites": {
            "smoke": {
                "ok": True,
                "paddle": {"steps": [{"name": "pp_formulanet_plus_l", "ok": True}]},
                "formula": {"steps": [{"name": "texteller_inference", "ok": True}]},
                "metrics": {
                    "formula": {"texteller": {"device": "cuda:0"}},
                    "runtime": {
                        "paddle_run_check": {
                            "cudnn_build": "9.9.0",
                            "cudnn_runtime": 90900,
                            "cudnn_match": True,
                        }
                    },
                },
            }
        },
    }

    benchmark_models.update_validation_lock(payload)

    lock = json.loads((tmp_path / "validation-lock.json").read_text(encoding="utf-8"))
    assert lock["components"]["texteller-1.0.2"]["device"] == "cuda:0"
    assert "warnings" not in lock["components"]["pp-formulanet-plus-l"]


def test_validation_lock_records_cudnn_mismatch_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(benchmark_models, "runtime_root", lambda: tmp_path)
    payload = {
        "generated_at": "2026-07-17T00:00:00+00:00",
        "suites": {
            "smoke": {
                "ok": True,
                "paddle": {"steps": [{"name": "pp_formulanet_plus_l", "ok": True}]},
                "formula": {"steps": []},
                "metrics": {
                    "formula": {"texteller": {}},
                    "runtime": {
                        "paddle_run_check": {
                            "cudnn_build": "9.9.0",
                            "cudnn_runtime": 90501,
                            "cudnn_match": False,
                        }
                    },
                },
            }
        },
    }

    benchmark_models.update_validation_lock(payload)

    lock = json.loads((tmp_path / "validation-lock.json").read_text(encoding="utf-8"))
    warning = lock["components"]["pp-formulanet-plus-l"]["warnings"][0]
    assert "build=9.9.0" in warning
    assert "runtime=90501" in warning
