"""Shared result and compatibility contracts for structured Word math conversion."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


CONVERSION_STATUSES = frozenset({"converted", "needs_review", "unsupported"})
ALLOWED_COMPATIBILITY_RULES = frozenset(
    {
        "eq-x-top-as-vector",
        "eq-overstrike-as-nuclear-scripts",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def conversion_issue(
    code: str,
    message: str,
    *,
    standard_reference: str,
    location: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "code": code,
        "message": message,
        "standard_reference": standard_reference,
    }
    if location:
        item["location"] = location
    if details:
        item["details"] = details
    return item


@dataclass
class MathConversionResult:
    status: str
    source_kind: str
    latex: str | None
    standard_reference: str
    warnings: list[dict[str, Any]] = field(default_factory=list)
    unsupported_constructs: list[dict[str, Any]] = field(default_factory=list)
    compatibility_rules: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in CONVERSION_STATUSES:
            raise ValueError(f"invalid conversion status: {self.status}")
        if self.status == "converted" and not self.latex:
            raise ValueError("converted result requires non-empty latex")
        if self.status != "converted" and self.latex is not None:
            raise ValueError("non-converted result must not expose partial latex")

    def require_latex(self) -> str:
        if self.status != "converted" or not self.latex:
            raise MathConversionError(self)
        return self.latex

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_kind": self.source_kind,
            "latex": self.latex,
            "standard_reference": self.standard_reference,
            "warnings": self.warnings,
            "unsupported_constructs": self.unsupported_constructs,
            "compatibility_rules": self.compatibility_rules,
        }


class MathConversionError(ValueError):
    def __init__(self, result: MathConversionResult):
        self.result = result
        codes = [item.get("code", "unknown") for item in result.unsupported_constructs]
        suffix = ", ".join(codes) if codes else result.status
        super().__init__(f"{result.source_kind} math conversion blocked: {suffix}")


@dataclass(frozen=True)
class CompatibilityOverride:
    source_kind: str
    formula_index: int
    instruction_sha256: str
    rule_id: str
    visual_evidence: str


class HashBoundCompatibilityOverrides:
    """Validated, source-bound legacy decisions for ambiguous EQ constructs."""

    def __init__(self, source_sha256: str, overrides: dict[tuple[str, int], CompatibilityOverride]):
        self.source_sha256 = source_sha256
        self._overrides = overrides

    @classmethod
    def empty(cls, source_sha256: str) -> "HashBoundCompatibilityOverrides":
        return cls(source_sha256, {})

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        source_sha256: str,
    ) -> "HashBoundCompatibilityOverrides":
        if payload.get("schema_version") != 1:
            raise ValueError("compatibility override schema_version must be 1")
        declared = str(payload.get("source_sha256") or "")
        if not SHA256_RE.fullmatch(source_sha256):
            raise ValueError("actual source_sha256 must be 64 lowercase hex characters")
        if declared != source_sha256:
            raise ValueError("compatibility override source_sha256 does not match the DOCX")

        records = payload.get("overrides")
        if not isinstance(records, list):
            raise ValueError("compatibility overrides must be a list")
        overrides: dict[tuple[str, int], CompatibilityOverride] = {}
        for raw in records:
            if not isinstance(raw, dict):
                raise ValueError("each compatibility override must be an object")
            source_kind = str(raw.get("source_kind") or "")
            formula_index = raw.get("formula_index")
            instruction_sha256 = str(raw.get("instruction_sha256") or "")
            rule_id = str(raw.get("rule_id") or "")
            evidence = str(raw.get("visual_evidence") or "").strip()
            if source_kind != "eq":
                raise ValueError("only source_kind=eq compatibility overrides are supported")
            if not isinstance(formula_index, int) or formula_index < 1:
                raise ValueError("formula_index must be a positive integer")
            if not SHA256_RE.fullmatch(instruction_sha256):
                raise ValueError("instruction_sha256 must be 64 lowercase hex characters")
            if rule_id not in ALLOWED_COMPATIBILITY_RULES:
                raise ValueError(f"unsupported compatibility rule: {rule_id}")
            if not evidence:
                raise ValueError("visual_evidence is required")
            key = (source_kind, formula_index)
            if key in overrides:
                raise ValueError(f"duplicate compatibility override: {source_kind}#{formula_index}")
            overrides[key] = CompatibilityOverride(
                source_kind=source_kind,
                formula_index=formula_index,
                instruction_sha256=instruction_sha256,
                rule_id=rule_id,
                visual_evidence=evidence,
            )
        return cls(source_sha256, overrides)

    def rule_for(self, formula_index: int, instruction: str) -> str | None:
        override = self._overrides.get(("eq", formula_index))
        if override is None:
            return None
        digest = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
        if digest != override.instruction_sha256:
            return None
        return override.rule_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_sha256": self.source_sha256,
            "overrides": [
                {
                    "source_kind": item.source_kind,
                    "formula_index": item.formula_index,
                    "instruction_sha256": item.instruction_sha256,
                    "rule_id": item.rule_id,
                    "visual_evidence": item.visual_evidence,
                }
                for item in sorted(self._overrides.values(), key=lambda value: value.formula_index)
            ],
        }
