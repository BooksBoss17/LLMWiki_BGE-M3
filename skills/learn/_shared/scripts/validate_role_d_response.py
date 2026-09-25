#!/usr/bin/env python
"""Validate Role D staged model responses and safely re-evaluate arithmetic."""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
from pathlib import Path
from typing import Any


STAGE_SECTIONS = {
    "confirm": {"parsed_question", "unconfirmed_items"},
    "hint": {"single_hint", "check_question"},
    "plan": {"model", "laws", "ordered_steps"},
    "solution": {"given", "model", "derivation", "answer", "verification", "common_errors"},
}
FORBIDDEN_EARLY_KEYS = {"answer", "final_answer", "result", "derivation", "substitution", "calculation_check"}
ALLOWED_BINOPS = {ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow}
ALLOWED_UNARYOPS = {ast.UAdd, ast.USub}


def safe_number_expression(expression: str) -> float:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 200:
        raise ValueError("calculation expression must be a non-empty string up to 200 characters")
    tree = ast.parse(expression, mode="eval")

    def evaluate(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_UNARYOPS:
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_BINOPS:
            left, right = evaluate(node.left), evaluate(node.right)
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                result = left / right
            else:
                if abs(right) > 8 or abs(left) > 1_000_000:
                    raise ValueError("unsafe exponent")
                result = left**right
            if not math.isfinite(result) or abs(result) > 1e100:
                raise ValueError("calculation result is not finite or is too large")
            return result
        raise ValueError(f"unsupported calculation node: {type(node).__name__}")

    return evaluate(tree)


def load_object(path: Path, label: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def validate_response(contract: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    stage = contract.get("stage")
    if stage not in STAGE_SECTIONS:
        raise ValueError("contract has invalid stage")
    if contract.get("status") != "ready_for_model":
        return {"ok": False, "status": "blocked", "issues": ["stage contract is blocked"], "calculation": None}
    required = {"schema_version", "stage", "action", "answer_kind", "sections"}
    missing = sorted(required - set(response))
    if missing:
        issues.append(f"missing response fields: {missing}")
    if response.get("schema_version") != 1:
        issues.append("response schema_version must be 1")
    if response.get("stage") != stage or response.get("action") != stage:
        issues.append("response stage/action does not match contract")
    if response.get("answer_kind") not in {"none", "numeric", "symbolic", "conceptual"}:
        issues.append("answer_kind must be none, numeric, symbolic, or conceptual")
    sections = response.get("sections")
    if not isinstance(sections, dict):
        issues.append("sections must be an object")
        sections = {}
    missing_sections = sorted(STAGE_SECTIONS[stage] - set(sections))
    if missing_sections:
        issues.append(f"missing required sections: {missing_sections}")
    if stage in {"confirm", "hint", "plan"}:
        leaked = sorted(FORBIDDEN_EARLY_KEYS & (set(response) | set(sections)))
        if leaked:
            issues.append(f"early stage contains answer-bearing fields: {leaked}")
        if response.get("answer_kind") != "none":
            issues.append("confirm/hint/plan answer_kind must be none")

    calculation_report: dict[str, Any] | None = None
    if stage == "solution" and response.get("answer_kind") == "numeric":
        calculation = response.get("calculation_check")
        if not isinstance(calculation, dict):
            issues.append("numeric solution requires calculation_check")
        else:
            required_calc = {"expression", "claimed_value", "unit", "retry_count"}
            if set(calculation) != required_calc:
                issues.append(f"calculation_check fields must be exactly: {sorted(required_calc)}")
            else:
                try:
                    computed = safe_number_expression(calculation["expression"])
                    claimed = calculation["claimed_value"]
                    retry_count = calculation["retry_count"]
                    if isinstance(claimed, bool) or not isinstance(claimed, (int, float)):
                        raise ValueError("claimed_value must be numeric")
                    if not isinstance(retry_count, int) or isinstance(retry_count, bool) or retry_count not in {0, 1}:
                        raise ValueError("retry_count must be 0 or 1")
                    if not isinstance(calculation["unit"], str) or not calculation["unit"].strip():
                        raise ValueError("unit must be a non-empty string")
                    if calculation["unit"].strip() not in str(sections.get("answer", "")):
                        raise ValueError("calculation unit must appear in the answer section")
                    consistent = math.isclose(computed, float(claimed), rel_tol=1e-9, abs_tol=1e-9)
                    calculation_report = {
                        "expression": calculation["expression"],
                        "computed_value": computed,
                        "claimed_value": float(claimed),
                        "unit": calculation["unit"],
                        "consistent": consistent,
                        "retry_count": retry_count,
                    }
                    if not consistent:
                        issues.append("claimed numeric value does not match safe arithmetic result")
                except Exception as exc:  # noqa: BLE001 - validation error is returned as data
                    issues.append(f"invalid calculation_check: {exc}")

    arithmetic_mismatch = any("numeric value does not match" in issue for issue in issues)
    retry_count = (calculation_report or {}).get("retry_count", 1)
    if arithmetic_mismatch and retry_count == 0:
        status = "retry_required"
    elif issues:
        status = "needs_confirmation"
    else:
        status = "accepted"
    return {"ok": not issues, "status": status, "issues": issues, "calculation": calculation_report}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a staged Role D model response.")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = validate_response(load_object(Path(args.contract), "contract"), load_object(Path(args.response), "response"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 3
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed
        error = {"ok": False, "status": "error", "error": type(exc).__name__, "message": str(exc)}
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
