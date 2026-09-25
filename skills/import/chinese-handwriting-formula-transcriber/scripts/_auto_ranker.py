import argparse
import json
import re
from pathlib import Path

import yaml


SHORT_REGION_IDS = {"q09", "q10", "q11", "q12", "q13"}
TEMPLATE_WORDS = (
    "满分",
    "答题",
    "班级",
    "姓名",
    "考场",
    "座号",
    "缺考",
    "注意事项",
    "第1页",
    "第2页",
    "第3页",
)
GARBAGE_PATTERNS = (
    "形形形",
    "�",
    "#",
    r"\ot\ominus",
    r"\subset\varnothing",
)
FORMULA_HINT = re.compile(r"[=+\-*/^_{}\\]|\\frac|\\sqrt|\\begin|[A-Za-z]\s*[_^]?|[πθΔΩμ]")
QMARK = "[[UNREADABLE]]"


def clean_text(value):
    value = str(value or "").replace("\r", " ").replace("\\n", "\n")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def strip_repr(value):
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
        value = value[1:-1]
    return value


def strip_question_prefix(text):
    text = clean_text(text)
    return re.sub(r"^\s*(?:1[0-6]|9|10|11|12|13)[.。．、\s]*", "", text)


def repetition_score(text):
    tokens = re.findall(r"\\[A-Za-z]+|[A-Za-z]+|\d+(?:\.\d+)?|[\u4e00-\u9fff]+|[=+\-*/^_{}()]", text)
    if not tokens:
        return 0.0
    counts = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return max(counts.values()) / max(1, len(tokens))


def latex_balance_ok(text):
    if text.count("{") != text.count("}"):
        return False
    if text.count("\\left") != text.count("\\right"):
        return False
    pairs = [("[", "]"), ("(", ")")]
    for left, right in pairs:
        if abs(text.count(left) - text.count(right)) >= 3:
            return False
    if "\\begin" in text and "\\end" not in text:
        return False
    return True


def reject_candidate(text, kind, stats=None):
    text = clean_text(strip_repr(text))
    if not text:
        return "empty"
    if any(word in text for word in TEMPLATE_WORDS):
        return "contains_score_or_template_text"
    if any(pattern in text for pattern in GARBAGE_PATTERNS):
        return "non_physics_garbage"
    if re.search(r"[\u3040-\u30ff]", text):
        return "non_physics_garbage"
    if kind == "formula" and not latex_balance_ok(text):
        return "latex_unbalanced"
    if text.count("\\qquad") >= 3:
        return "high_repetition"
    rep = repetition_score(text)
    if rep >= 0.25 and len(text) > 70:
        return "high_repetition"
    if text.count("\\frac") >= 7:
        return "high_repetition"
    if kind == "formula" and len(text) > 420:
        return "output_too_long"
    if stats and float(stats.get("ink_ratio") or 0.0) < 0.002 and len(text) > 12:
        return "low_ink_hallucination"
    return None


def machine_status(score, final_threshold=0.84, candidate_threshold=0.55, rejected=False):
    if rejected:
        return "machine_abstain"
    if score >= final_threshold:
        return "machine_final"
    if score >= candidate_threshold:
        return "machine_candidate"
    return "machine_abstain"


def score_text_candidate(text, scores, stats):
    text = clean_text(text)
    if not text:
        return 0.0
    score_values = [float(score) for score in scores or [] if isinstance(score, (int, float))]
    score = sum(score_values) / len(score_values) if score_values else 0.58
    if len(text) <= 2:
        score -= 0.12
    if any(word in text for word in TEMPLATE_WORDS):
        score -= 0.3
    if float((stats or {}).get("ink_ratio") or 0.0) < 0.003:
        score -= 0.2
    if reject_candidate(text, "text", stats):
        score -= 0.45
    return max(0.0, min(0.97, score))


def score_formula_candidate(text, source, stats):
    text = clean_text(strip_repr(text))
    if not text:
        return 0.0
    base = {"pp_formulanet": 0.54, "texteller": 0.56}.get(source, 0.5)
    if FORMULA_HINT.search(text):
        base += 0.08
    if len(text) < 8:
        base -= 0.18
    if len(text) > 220:
        base -= 0.12
    if repetition_score(text) > 0.18:
        base -= 0.18
    if reject_candidate(text, "formula", stats):
        base -= 0.5
    if float((stats or {}).get("ink_ratio") or 0.0) < 0.004:
        base -= 0.18
    return max(0.0, min(0.9, base))


def extract_texteller_candidate(value):
    if not isinstance(value, dict) or value.get("status") != "ok":
        return []
    latex = clean_text(value.get("latex") or "")
    return [latex] if latex else []


def formula_by_line(formula_report):
    mapping = {}
    for region in (formula_report or {}).get("regions", []):
        for line in region.get("lines", []):
            mapping[line["line_id"]] = line
    return mapping


def line_status(kind, confidence, rejected):
    threshold = 0.78 if kind == "formula" else 0.80
    return machine_status(confidence, final_threshold=threshold, candidate_threshold=0.55, rejected=rejected)


def rank_line(line, formula_line):
    line_type = line.get("line_type") or "unknown"
    stats = line.get("stats") or {}
    text_result = line.get("paddle_text") or {}
    text = clean_text(text_result.get("joined_text") or " ".join(map(str, text_result.get("texts") or [])))
    alternatives = []

    text_reject = reject_candidate(text, "text", stats)
    text_confidence = score_text_candidate(text, text_result.get("scores"), stats)
    alternatives.append(
        {
            "source": "paddle_text",
            "kind": "text",
            "raw": text,
            "normalized": text,
            "confidence": round(text_confidence, 3),
            "quality_score": round(text_confidence, 3),
            "status": line_status("text", text_confidence, bool(text_reject)),
            "reject_reason": text_reject,
            "risk_flags": [text_reject] if text_reject else [],
        }
    )

    if line.get("pp_formulanet"):
        for formula in (line.get("pp_formulanet") or {}).get("formulas") or []:
            reason = reject_candidate(formula, "formula", stats)
            confidence = score_formula_candidate(formula, "pp_formulanet", stats)
            alternatives.append(
                {
                    "source": "pp_formulanet",
                    "kind": "formula",
                    "raw": formula,
                    "normalized": clean_text(formula),
                    "confidence": round(confidence, 3),
                    "quality_score": round(confidence, 3),
                    "status": line_status("formula", confidence, bool(reason)),
                    "reject_reason": reason,
                    "risk_flags": [reason] if reason else [],
                }
            )

    if formula_line:
        for candidate in extract_texteller_candidate(formula_line.get("texteller")):
            reason = reject_candidate(candidate, "formula", stats)
            confidence = score_formula_candidate(candidate, "texteller", stats)
            alternatives.append(
                {
                    "source": "texteller",
                    "kind": "formula",
                    "raw": candidate,
                    "normalized": clean_text(candidate),
                    "confidence": round(confidence, 3),
                    "quality_score": round(confidence, 3),
                    "status": line_status("formula", confidence, bool(reason)),
                    "reject_reason": reason,
                    "risk_flags": [reason] if reason else [],
                }
            )

    wanted_kind = "formula" if line_type == "formula" else None
    if line_type == "mixed":
        wanted_kind = None
    valid = [alt for alt in alternatives if alt["status"] != "machine_abstain" and alt["normalized"]]
    if wanted_kind:
        kind_valid = [alt for alt in valid if alt["kind"] == wanted_kind]
        if kind_valid:
            valid = kind_valid
    if not valid:
        best = max(alternatives, key=lambda item: item["confidence"], default=None)
        reason = best.get("reject_reason") if best else "no_candidate"
        return {
            "line_id": line["line_id"],
            "line_type": line_type,
            "final_candidate": None,
            "confidence": 0.0,
            "quality_score": 0.0,
            "status": "machine_abstain",
            "reject_reason": reason,
            "risk_flags": [reason] if reason else [],
            "alternatives": alternatives,
        }

    best = max(valid, key=lambda item: item["confidence"])
    return {
        "line_id": line["line_id"],
        "line_type": line_type,
        "final_candidate": best["normalized"],
        "confidence": best["confidence"],
        "quality_score": best["quality_score"],
        "status": best["status"],
        "reject_reason": best.get("reject_reason"),
        "risk_flags": best.get("risk_flags", []),
        "alternatives": sorted(alternatives, key=lambda item: item["confidence"], reverse=True)[:8],
    }


def first_allowed_match(text, allowed, corrections=None):
    text = clean_text(text)
    corrections = corrections or {}
    for src, dst in corrections.items():
        if src in text:
            return dst
    for item in allowed:
        if re.search(rf"(?<![A-Za-z]){re.escape(str(item))}(?![A-Za-z])", text):
            return str(item)
    return None


def parse_q09(text, schema):
    body = strip_question_prefix(text).upper()
    letters = re.findall(r"[ABCD]", body)
    value = letters[0] if letters else first_allowed_match(body, schema.get("allowed", []), schema.get("corrections"))
    flags = [] if value else ["choice_unparsed"]
    return {"answer": value}, flags


def parse_q10(text, schema):
    body = strip_question_prefix(text)
    corrections = schema.get("corrections") or {}
    allowed = schema.get("slots", [{}])[0].get("allowed", [])
    parts = re.split(r"[①②12][.。．、\s]*|[:：;；，,]", body)
    parts = [part for part in parts if clean_text(part)]
    slot_values = []
    for part in parts:
        value = first_allowed_match(part, allowed, corrections)
        if value:
            slot_values.append(value)
    while len(slot_values) < 2:
        slot_values.append(None)
    flags = []
    if slot_values[0] is None:
        flags.append("slot_1_unparsed")
    if slot_values[1] is None:
        flags.append("slot_2_unparsed")
    return {"slot_1": slot_values[0], "slot_2": slot_values[1]}, flags


def parse_q11(text, schema):
    body = strip_question_prefix(text).replace("：", ":")
    numbers = re.findall(r"\d+(?:\.\d+)?\s*(?:W|J|N|Pa)?", body, re.I)
    ratios = re.findall(r"\d+\s*:\s*\d+", body)
    part1 = clean_text(numbers[0]) if numbers else None
    part2 = clean_text(ratios[0]) if ratios else None
    flags = []
    if part1 is None:
        flags.append("part_1_unparsed")
    if part2 is None:
        flags.append("part_2_unparsed")
    return {"part_1": part1, "part_2": part2}, flags


def parse_q12(text, schema):
    body = strip_question_prefix(text)
    corrections = schema.get("corrections") or {}
    direction = first_allowed_match(body, schema.get("directions", []), corrections)
    if direction in {"左", "右", "上", "下"}:
        direction = "向" + direction
    choices = []
    for choice in sorted(schema.get("choices", []), key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z]){re.escape(choice)}(?![A-Za-z])", body):
            choices.append(choice)
            break
    flags = []
    if direction is None:
        flags.append("direction_unparsed")
    if not choices:
        flags.append("choice_unparsed")
    return {"direction": direction, "choice": choices[0] if choices else None}, flags


def parse_q13(text, schema):
    body = strip_question_prefix(text)
    max_chars = int(schema.get("max_chars") or 80)
    flags = []
    if len(body) > max_chars:
        flags.append("too_long")
        body = body[:max_chars]
    if reject_candidate(body, "text"):
        flags.append("noisy_text")
    return {"text": body or None}, flags if body else ["text_unparsed"]


def parse_short_answer(region_id, raw_text, schema):
    qid = region_id.lower()
    if qid == "q09":
        parsed, flags = parse_q09(raw_text, schema)
        final = parsed.get("answer")
    elif qid == "q10":
        parsed, flags = parse_q10(raw_text, schema)
        values = [parsed.get("slot_1") or QMARK, parsed.get("slot_2") or QMARK]
        final = f"① {values[0]}；② {values[1]}"
    elif qid == "q11":
        parsed, flags = parse_q11(raw_text, schema)
        final = "；".join(value for value in [parsed.get("part_1"), parsed.get("part_2")] if value)
    elif qid == "q12":
        parsed, flags = parse_q12(raw_text, schema)
        final = "；".join(value for value in [parsed.get("direction"), parsed.get("choice")] if value)
    elif qid == "q13":
        parsed, flags = parse_q13(raw_text, schema)
        final = parsed.get("text")
    else:
        parsed, flags, final = {}, ["unknown_short_question"], None
    return parsed, flags, final


def rank_short_region(manifest_region, ranked_lines, schemas):
    region_id = manifest_region["id"]
    schema = schemas.get(region_id, {})
    raw_text = " ".join(line.get("final_candidate") or "" for line in ranked_lines).strip()
    parsed, flags, final = parse_short_answer(region_id, raw_text, schema)
    line_scores = [line["confidence"] for line in ranked_lines if line.get("final_candidate")]
    ocr_score = sum(line_scores) / len(line_scores) if line_scores else 0.0
    template_score = 1.0 if not flags else max(0.0, 1.0 - 0.35 * len(flags))
    quality = round(0.55 * ocr_score + 0.45 * template_score, 3)
    min_final = float(schema.get("min_final_confidence") or 0.84)
    if not final:
        status = "machine_abstain"
    elif flags:
        status = machine_status(quality, final_threshold=0.98, candidate_threshold=0.50)
    else:
        status = machine_status(quality, final_threshold=min_final, candidate_threshold=0.50)
    if status == "machine_final" and schema.get("allow_machine_final") is False:
        status = "machine_candidate"
        flags = [*flags, "schema_not_strict_final"]
    if status == "machine_abstain":
        final = None
    reject_reason = None
    if status == "machine_abstain":
        reject_reason = "template_unparsed"
    elif status == "machine_candidate" and flags:
        reject_reason = flags[0]
    return {
        "region_id": region_id,
        "question_id": manifest_region["label"],
        "region_type": manifest_region["region_type"],
        "final_candidate": final,
        "confidence": quality,
        "quality_score": quality,
        "status": status,
        "reject_reason": reject_reason,
        "risk_flags": flags,
        "parsed_fields": parsed,
        "alternatives": [],
        "lines": ranked_lines,
    }


def rank_long_region(manifest_region, ranked_lines):
    accepted_text = [
        line["final_candidate"]
        for line in ranked_lines
        if line.get("final_candidate") and line["status"] != "machine_abstain"
    ]
    scores = [line["confidence"] for line in ranked_lines if line.get("final_candidate")]
    confidence = round(sum(scores) / len(scores), 3) if scores else 0.0
    if not accepted_text:
        status = "machine_abstain"
    else:
        # 在本地自动阶段，完整长答题区域永不提升为 machine_final。
        status = "machine_candidate"
    flags = []
    if any(line["status"] == "machine_abstain" for line in ranked_lines):
        flags.append("contains_abstained_lines")
    if any(line.get("risk_flags") for line in ranked_lines):
        flags.append("contains_risk_flags")
    return {
        "region_id": manifest_region["id"],
        "question_id": manifest_region["label"],
        "region_type": manifest_region["region_type"],
        "final_candidate": "\n".join(accepted_text) if accepted_text else None,
        "confidence": confidence,
        "quality_score": confidence,
        "status": status,
        "reject_reason": "no_line_candidate" if not accepted_text else None,
        "risk_flags": flags,
        "parsed_fields": {},
        "alternatives": [],
        "lines": ranked_lines,
    }


def write_markdown(path, transcript):
    lines = [
        f"# 自动 OCR 转写：{transcript['task_id']}",
        "",
        f"- 源图像：`{transcript['source_image']}`",
        f"- 状态：`{transcript['status']}`",
        "",
    ]
    for region in transcript["regions"]:
        lines.extend(
            [
                f"## Q{region['question_id']}",
                "",
                f"- 状态：`{region['status']}`",
                f"- 置信度：`{region['confidence']}`",
                f"- 风险标记：`{', '.join(region.get('risk_flags') or [])}`",
                "",
                "```text",
                region.get("final_candidate") or "",
                "```",
                "",
            ]
        )
        low_lines = [line for line in region.get("lines", []) if line["status"] != "machine_final"]
        if low_lines:
            lines.extend(["候选/弃权文本行：", ""])
            for line in low_lines[:12]:
                lines.append(
                    f"- `{line['line_id']}` {line['line_type']} {line['status']} {line.get('reject_reason') or ''}"
                )
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--source-image", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--paddle-result", required=True)
    parser.add_argument("--formula-result", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--schemas", default="")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    paddle = json.loads(Path(args.paddle_result).read_text(encoding="utf-8"))
    formula = json.loads(Path(args.formula_result).read_text(encoding="utf-8")) if Path(args.formula_result).exists() else {}
    schemas = yaml.safe_load(Path(args.schemas).read_text(encoding="utf-8")) if args.schemas else {}
    formula_lines = formula_by_line(formula)

    paddle_regions = {region["id"]: region for region in paddle.get("regions", [])}
    regions = []
    for manifest_region in manifest["regions"]:
        region_id = manifest_region["id"]
        paddle_region = paddle_regions.get(region_id, {})
        ranked_lines = [
            rank_line(line, formula_lines.get(line["line_id"]))
            for line in paddle_region.get("lines", [])
        ]
        if region_id in SHORT_REGION_IDS:
            regions.append(rank_short_region(manifest_region, ranked_lines, schemas))
        else:
            regions.append(rank_long_region(manifest_region, ranked_lines))

    transcript = {
        "task_id": args.task_id,
        "source_image": args.source_image,
        "created_by": "chinese-handwriting-formula-transcriber:auto_local_v2",
        "status": "ok" if any(region["final_candidate"] for region in regions) else "machine_abstain",
        "regions": regions,
    }
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "auto_transcript.json"
    md_path = out_dir / "auto_transcript.md"
    json_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(md_path, transcript)
    print(json.dumps({"ok": transcript["status"] == "ok", "json": str(json_path), "markdown": str(md_path)}, ensure_ascii=False, indent=2))
    return 0 if transcript["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
