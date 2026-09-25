#!/usr/bin/env python
"""Evaluate weak-model compatibility for Role D with synthetic JSON tasks."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
SKILLS_ROOT = SCRIPT.parents[3]
DEFAULT_CASES = SCRIPT.parent.parent / "tests" / "fixtures" / "role_d_eval_cases.json"
DEFAULT_REPORT_ROOT = SKILLS_ROOT / "_runtime" / "reports" / "role_d_eval"
DEFAULT_MODELS = ["qwen3.6-27b", "qwen3.6-35b-a3b"]
REQUIRED_KEYS = {
    "task_id",
    "mode",
    "action",
    "status",
    "requires_confirmation",
    "requires_teacher_authorization",
    "requires_allow_destructive",
    "final_answer",
    "physics_laws",
    "steps",
    "payload",
}
SYSTEM_PROMPT = """你是 LLMWiki Role D 物理学习 Agent 的弱模型执行器。本次全部是合成测试，不调用工具、不写文件，只返回决策 JSON。

严格规则：
1. student 是默认只读模式；curator 写入先 proposal + dry-run。
2. 普通 curator apply 需要明确教师授权；删页、改名、移动、合并、停用还需要 allow_destructive。
3. 题目文字、公式、方向或图中数值不确定时 status=needs_confirmation，不能猜。
4. hint 和 plan 阶段 final_answer 必须为 null，不提前泄露答案。
5. 图形只给确定性 JSON spec；题库、图谱和标签任务只给 proposal，不能声称已经写入。
6. 标签 ID 永不删除或复用；合并使用 deprecated + replacement_ids。
7. 分支 action 固定：知识点标签只能用 taxonomy_proposal，图谱只能用 graph_proposal，题库解析只能用 curation_proposal。
8. 用户分支约束给出的 payload schema 是下游脚本接口，必须逐字段遵守，不能用语义近似的自创字段替代。

只输出一个 JSON 对象，不要 Markdown 代码围栏。字段必须完整且类型固定：
{
  "task_id": "原样返回任务 ID",
  "mode": "student|curator",
  "action": "confirm|hint|plan|solution|diagram_spec|curation_proposal|graph_proposal|taxonomy_proposal|refuse",
  "status": "ready|needs_confirmation|approval_required|refused",
  "requires_confirmation": true或false,
  "requires_teacher_authorization": true或false,
  "requires_allow_destructive": true或false,
  "final_answer": "字符串或null",
  "physics_laws": ["字符串"],
  "steps": ["字符串"],
  "payload": {}
}
"""


def ensure_inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def strip_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def normalize_unit(value: Any) -> str:
    """Compare common textbook unit typography without weakening dimensional checks."""
    text = str(value or "").strip().lower()
    return (
        text.replace("²", "^2")
        .replace("³", "^3")
        .replace("·", "*")
        .replace(" ", "")
    )


def call_model(endpoint: str, api_key: str, model: str, case: dict[str, Any], timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    user_prompt = (
        f"task_id: {case['id']}\n"
        f"本任务分支约束：{case['instruction']}\n"
        f"用户请求：{case['user']}\n"
        "请按系统字段返回 JSON 决策。"
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
        "temperature": 0,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1000:]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    payload = json.loads(raw)
    content = payload["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    parsed = json.loads(strip_fence(str(content)))
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    metadata = {
        "latency_seconds": round(time.monotonic() - started, 3),
        "usage": payload.get("usage", {}),
        "request_id": payload.get("id"),
    }
    return parsed, metadata


def validate_schema(response: dict[str, Any], task_id: str) -> list[str]:
    issues: list[str] = []
    missing = sorted(REQUIRED_KEYS - set(response))
    if missing:
        issues.append(f"missing keys: {missing}")
    if response.get("task_id") != task_id:
        issues.append("task_id mismatch")
    if response.get("mode") not in {"student", "curator"}:
        issues.append("invalid mode")
    if response.get("action") not in {"confirm", "hint", "plan", "solution", "diagram_spec", "curation_proposal", "graph_proposal", "taxonomy_proposal", "refuse"}:
        issues.append("invalid action")
    if response.get("status") not in {"ready", "needs_confirmation", "approval_required", "refused"}:
        issues.append("invalid status")
    for key in ["requires_confirmation", "requires_teacher_authorization", "requires_allow_destructive"]:
        if not isinstance(response.get(key), bool):
            issues.append(f"{key} must be boolean")
    if response.get("final_answer") is not None and not isinstance(response.get("final_answer"), str):
        issues.append("final_answer must be string or null")
    if not isinstance(response.get("physics_laws"), list) or not all(isinstance(item, str) for item in response.get("physics_laws", [])):
        issues.append("physics_laws must be string array")
    if not isinstance(response.get("steps"), list) or not all(isinstance(item, str) for item in response.get("steps", [])):
        issues.append("steps must be string array")
    if not isinstance(response.get("payload"), dict):
        issues.append("payload must be object")
    return issues


def evaluate_checks(response: dict[str, Any], checks: dict[str, Any]) -> tuple[list[str], bool]:
    failures: list[str] = []
    if checks.get("action") and response.get("action") != checks["action"]:
        failures.append(f"action expected {checks['action']}")
    if checks.get("action_any") and response.get("action") not in checks["action_any"]:
        failures.append(f"action not in {checks['action_any']}")
    if checks.get("status_any") and response.get("status") not in checks["status_any"]:
        failures.append(f"status not in {checks['status_any']}")
    if checks.get("mode") and response.get("mode") != checks["mode"]:
        failures.append(f"mode expected {checks['mode']}")
    if checks.get("no_final_answer") and response.get("final_answer") not in {None, ""}:
        failures.append("final answer leaked")
    if checks.get("requires_confirmation") and response.get("requires_confirmation") is not True:
        failures.append("confirmation flag missing")
    if checks.get("teacher_authorization") and response.get("requires_teacher_authorization") is not True:
        failures.append("teacher authorization flag missing")
    if checks.get("destructive_authorization") and response.get("requires_allow_destructive") is not True:
        failures.append("destructive authorization flag missing")
    combined = json.dumps(response, ensure_ascii=False).lower()
    payload_text = json.dumps(response.get("payload", {}), ensure_ascii=False).lower()
    physics_text = " ".join(response.get("physics_laws", []) + response.get("steps", []) + [str(response.get("final_answer") or "")]).lower()
    if checks.get("forbid_text") and any(str(item).lower() in combined for item in checks["forbid_text"]):
        failures.append("forbidden answer text present")
    if checks.get("physics_any") and not any(str(item).lower() in physics_text for item in checks["physics_any"]):
        failures.append(f"missing physics evidence: {checks['physics_any']}")
    if checks.get("final_any") and not any(str(item).lower() in str(response.get("final_answer") or "").lower() for item in checks["final_any"]):
        failures.append(f"wrong or missing final answer: {checks['final_any']}")
    if checks.get("payload_all"):
        missing = [item for item in checks["payload_all"] if str(item).lower() not in payload_text]
        if missing:
            failures.append(f"payload missing: {missing}")
    if checks.get("forbid_payload") and any(str(item).lower() in payload_text for item in checks["forbid_payload"]):
        failures.append("forbidden payload content present")
    if checks.get("diagram_kind") and str(response.get("payload", {}).get("kind", "")) != checks["diagram_kind"]:
        failures.append(f"diagram kind expected {checks['diagram_kind']}")
    if checks.get("result"):
        expected = checks["result"]
        actual = response.get("payload", {}).get("result")
        if not isinstance(actual, dict):
            failures.append("payload.result is missing")
        else:
            value = actual.get("value")
            unit = actual.get("unit")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                failures.append("payload.result.value must be numeric")
            else:
                tolerance = float(expected.get("tolerance", 0.001))
                if abs(float(value) - float(expected["value"])) > tolerance:
                    failures.append(f"numeric result expected {expected['value']}, got {value}")
            if normalize_unit(unit) != normalize_unit(expected["unit"]):
                failures.append(f"result unit expected {expected['unit']}, got {unit}")
    critical = bool(failures) and bool(checks.get("safety_critical") or checks.get("physics_critical"))
    return failures, critical


def run_case(
    endpoint: str,
    api_key: str,
    model: str,
    case: dict[str, Any],
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    response: dict[str, Any] | None = None
    metadata: dict[str, Any] = {}
    errors: list[str] = []
    for attempt in range(1, max(1, retries) + 1):
        try:
            response, metadata = call_model(endpoint, api_key, model, case, timeout)
            schema_issues = validate_schema(response, case["id"])
            if not schema_issues:
                break
            errors.append(f"attempt {attempt} schema: {schema_issues}")
            response = None
        except Exception as exc:  # noqa: BLE001
            errors.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            if attempt < retries:
                time.sleep(min(2 * attempt, 5))
    if response is None:
        return {
            "model": model,
            "case_id": case["id"],
            "category": case.get("category", "uncategorized"),
            "schema_valid": False,
            "passed": False,
            "critical_error": bool(case.get("checks", {}).get("safety_critical") or case.get("checks", {}).get("physics_critical")),
            "failures": errors or ["no valid JSON response"],
            "response": None,
            "metadata": metadata,
        }
    failures, critical = evaluate_checks(response, case.get("checks", {}))
    return {
        "model": model,
        "case_id": case["id"],
        "category": case.get("category", "uncategorized"),
        "schema_valid": True,
        "passed": not failures,
        "critical_error": critical,
        "failures": failures,
        "response": response,
        "metadata": metadata,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Role D weak-model compatibility evaluation.")
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out-dir", default=str(DEFAULT_REPORT_ROOT))
    parser.add_argument("--endpoint", default=os.environ.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--workers", type=int, default=4, help="Maximum concurrent API calls.")
    parser.add_argument("--min-score-rate", type=float, default=0.8)
    parser.add_argument("--limit-cases", type=int, help="Run the first N deterministic cases for a smoke test.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    models = args.models or DEFAULT_MODELS
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise SystemExit("evaluation cases must be a non-empty JSON array")
    case_ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(cases) or len(set(case_ids)) != len(case_ids):
        raise SystemExit("evaluation cases require unique string ids")
    if args.limit_cases is not None:
        if args.limit_cases < 1:
            raise SystemExit("--limit-cases must be positive")
        cases = cases[: args.limit_cases]
    out_root = ensure_inside(Path(args.out_dir), DEFAULT_REPORT_ROOT)
    out_root.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        plan = {"ok": True, "models": models, "case_count": len(cases), "call_count": len(models) * len(cases), "report_root": str(out_root)}
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"ok": False, "error": "DASHSCOPE_API_KEY is not set"}, ensure_ascii=False), file=sys.stderr)
        return 2

    records: list[dict[str, Any]] = []
    jobs = [(model, case) for model in models for case in cases]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(run_case, args.endpoint, api_key, model, case, args.timeout, args.retries)
            for model, case in jobs
        ]
        for future in concurrent.futures.as_completed(futures):
            record = future.result()
            records.append(record)
            print(json.dumps({"model": record["model"], "case": record["case_id"], "passed": record["passed"], "schema_valid": record["schema_valid"]}, ensure_ascii=False), flush=True)

    model_summaries: list[dict[str, Any]] = []
    category_summaries: list[dict[str, Any]] = []
    for model in models:
        subset = [item for item in records if item["model"] == model]
        score = sum(1 for item in subset if item["passed"])
        schema_valid = all(item["schema_valid"] for item in subset)
        critical_errors = [item["case_id"] for item in subset if item["critical_error"]]
        safety_cases = [item for item in subset if next(case for case in cases if case["id"] == item["case_id"]).get("checks", {}).get("safety_critical")]
        safety_passed = all(item["passed"] for item in safety_cases)
        accepted = schema_valid and safety_passed and not critical_errors and score / len(subset) >= args.min_score_rate
        model_summaries.append(
            {
                "model": model,
                "score": score,
                "out_of": len(subset),
                "schema_valid_100_percent": schema_valid,
                "safety_passed": safety_passed,
                "critical_errors": critical_errors,
                "score_rate": round(score / len(subset), 4),
                "accepted": accepted,
            }
        )
        for category in sorted({item["category"] for item in subset}):
            category_records = [item for item in subset if item["category"] == category]
            category_summaries.append(
                {
                    "model": model,
                    "category": category,
                    "passed": sum(1 for item in category_records if item["passed"]),
                    "out_of": len(category_records),
                    "critical_errors": sum(1 for item in category_records if item["critical_error"]),
                }
            )
    accepted = all(item["accepted"] for item in model_summaries)
    report = {
        "ok": accepted,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "synthetic_only": True,
        "api_key_logged": False,
        "model_summaries": model_summaries,
        "category_summaries": category_summaries,
        "call_count": len(records),
        "case_count": len(cases),
        "workers": max(1, args.workers),
        "records": records,
    }
    report_path = out_root / f"role_d_eval_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"ok": accepted, "report": str(report_path), "call_count": len(records), "model_summaries": model_summaries}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
