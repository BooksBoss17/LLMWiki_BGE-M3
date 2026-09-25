"""Conservative quality contract for image-derived formula candidates."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from fix_chinese_subscripts import scan_for_garbled


ENGINE_ORDER = ("mtef", "pp_formulanet", "texteller")
ACTIVE_ENGINES = frozenset(ENGINE_ORDER)
DEFAULT_REQUIRED_ENGINES = ("pp_formulanet", "texteller")


def normalize_latex(latex: str) -> str:
    """Normalize only presentation noise; never rewrite mathematical meaning."""
    text = str(latex or "").strip()
    text = re.sub(r"^\$+|\$+$", "", text).strip()
    if (text.startswith(r"\[") and text.endswith(r"\]")) or (
        text.startswith(r"\(") and text.endswith(r"\)")
    ):
        text = text[2:-2].strip()
    text = text.replace(r"\left", "").replace(r"\right", "")
    text = re.sub(r"\s+", "", text)
    return text


def assess_formula_candidate(
    latex: str,
    score: float | None,
    *,
    engine: str,
    final_threshold: float = 0.92,
) -> dict[str, Any]:
    text = str(latex or "").strip()
    numeric_score = float(score or 0.0)
    flags: list[str] = []
    if not text:
        flags.append("empty_candidate")
    if "�" in text:
        flags.append("replacement_character")
    if score is not None and numeric_score < final_threshold:
        flags.append("low_confidence")
    if scan_for_garbled(f"${text}$", engine=engine):
        flags.append("unresolved_engine_specific_garble")
    if re.search(r"[\u3400-\u9fff]", text):
        flags.append("chinese_formula_text")
    if re.search(r"\\(?:vec|rightharpoonup|overrightarrow|mathbf)\b", text):
        flags.append("vector_notation")
    if re.search(r"\{\}\s*[_^]\{|[_^]\{[^}]+\}.*[_^]\{[^}]+\}", text):
        flags.append("nuclear_or_multi_script")
    if re.fullmatch(r"[A-Za-z0-9α-ωΑ-Ω]", normalize_latex(text)):
        flags.append("single_character_formula")
    if re.search(r"\\(?:frac|dfrac|tfrac|sqrt)\b", text):
        flags.append("fraction_or_root")
    if text.count("\\frac") >= 2 or re.search(r"\\(?:begin\{(?:matrix|cases)|sum|int)\b", text):
        flags.append("complex_formula_structure")

    if "empty_candidate" in flags or (score is not None and numeric_score < 0.55):
        status = "machine_abstain"
    elif flags:
        status = "machine_candidate"
    else:
        # A single local model is evidence, never a final decision.  The
        # multi-engine resolver below is the only machine-final seam.
        status = "machine_candidate"
    return {
        "engine": engine,
        "text": text,
        "confidence": numeric_score if score is not None else None,
        "status": status,
        "risk_flags": sorted(set(flags)),
    }


def resolve_formula_consensus(
    candidates: list[dict[str, Any]],
    *,
    geometry_issues: list[str] | None = None,
    required_engines: tuple[str, ...] = DEFAULT_REQUIRED_ENGINES,
) -> dict[str, Any]:
    """Require every declared independent engine to agree before acceptance."""
    required = frozenset(str(value) for value in required_engines if value)
    if len(required) < 2:
        raise ValueError("formula consensus requires at least two distinct engines")
    assessed: list[dict[str, Any]] = []
    groups: dict[str, list[dict[str, Any]]] = {}
    all_flags: set[str] = set()
    for raw in candidates:
        engine = str(raw.get("engine") or "unknown")
        item = assess_formula_candidate(
            str(raw.get("text") or raw.get("latex") or ""),
            raw.get("confidence", raw.get("score")),
            engine=engine,
        )
        for flag in raw.get("risk_flags") or []:
            item["risk_flags"].append(str(flag))
        if engine not in ACTIVE_ENGINES:
            item["risk_flags"].append("disabled_by_profile")
        item["risk_flags"] = sorted(set(item["risk_flags"]))
        item["normalized_latex"] = normalize_latex(item["text"])
        assessed.append(item)
        all_flags.update(item["risk_flags"])
        if item["status"] != "machine_abstain" and not item["risk_flags"] and item["normalized_latex"]:
            groups.setdefault(item["normalized_latex"], []).append(item)

    geometry = sorted(set(str(value) for value in (geometry_issues or []) if value))
    if geometry:
        all_flags.add("render_geometry_risk")

    winner: tuple[str, list[dict[str, Any]]] | None = None
    for latex, members in groups.items():
        distinct_engines = {item["engine"] for item in members}
        if required.issubset(distinct_engines) and (
            winner is None or len(distinct_engines) > len({i["engine"] for i in winner[1]})
        ):
            winner = (latex, members)

    usable_normalized = {
        item["normalized_latex"]
        for item in assessed
        if item["status"] != "machine_abstain" and item["normalized_latex"]
    }
    if len(usable_normalized) > 1 and winner is None:
        all_flags.add("engine_disagreement")

    if winner is not None and not geometry:
        latex, members = winner
        agreed = [item["engine"] for item in members]
        agreed.sort(key=lambda value: ENGINE_ORDER.index(value) if value in ENGINE_ORDER else len(ENGINE_ORDER))
        return {
            "status": "machine_final",
            "final_latex": latex,
            "agreed_engines": agreed,
            "required_engines": sorted(required),
            "candidates": assessed,
            "risk_flags": [],
            "geometry_issues": geometry,
        }

    if not assessed or all(item["status"] == "machine_abstain" for item in assessed):
        status = "machine_abstain"
    else:
        status = "needs_vlm"
    return {
        "status": status,
        "final_latex": None,
        "agreed_engines": [],
        "required_engines": sorted(required),
        "candidates": assessed,
        "risk_flags": sorted(all_flags),
        "geometry_issues": geometry,
    }


def make_formula_review_task(
    candidate_id: str,
    source_image: str | Path,
    candidate: dict[str, Any],
    *,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    image_path = Path(source_image)
    if source_sha256 is None and image_path.is_file():
        source_sha256 = hashlib.sha256(image_path.read_bytes()).hexdigest()
    return {
        "candidate_id": candidate_id,
        "status": "unresolved",
        "source_image": str(source_image),
        "source_sha256": source_sha256,
        "candidate": candidate,
        "required_engines": ["pp_formulanet", "texteller"],
        "instruction": (
            "Inspect the rendered formula image. Compare PP-FormulaNet and TexTeller candidates; "
            "use agent VLM for unresolved Chinese subscripts, nuclear symbols, vectors, fractions, or signs. "
            "Return source-evidenced LaTeX and do not guess unreadable symbols."
        ),
        "expected_result_schema": {
            "candidate_id": candidate_id,
            "status": "resolved|unresolved",
            "source_sha256": source_sha256 or "64 lowercase hex characters",
            "final_latex": "string",
            "visual_evidence": "short visual evidence",
            "confidence": "0.0-1.0",
        },
    }
